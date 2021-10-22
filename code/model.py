import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

import utils

import skimage
from skimage.util import img_as_float

from prototyping import view_as_windows

EPS = 1e-20

class CRW(nn.Module):
    def __init__(self, args, vis=None):
        super(CRW, self).__init__()
        self.args = args

        self.edgedrop_rate = getattr(args, "dropout", 0)
        self.featdrop_rate = getattr(args, "featdrop", 0)
        self.temperature = getattr(args, "temp", getattr(args, "temperature", 0.07))

        self.encoder = utils.make_encoder(args).to(self.args.device)
        self.infer_dims()
        self.selfsim_fc = self.make_head(depth=getattr(args, "head_depth", 0))

        self.xent = nn.CrossEntropyLoss(reduction="none")
        self._xent_targets = dict()

        self.dropout = nn.Dropout(p=self.edgedrop_rate, inplace=False)
        self.featdrop = nn.Dropout(p=self.featdrop_rate, inplace=False)

        self.flip = getattr(args, "flip", False)
        self.sk_targets = getattr(args, "sk_targets", False)
        self.vis = vis

    def infer_dims(self):
        in_sz = 256
        dummy = torch.zeros(1, 3, 1, in_sz, in_sz).to(next(self.encoder.parameters()).device)
        dummy_out = self.encoder(dummy)
        self.enc_hid_dim = dummy_out.shape[1]
        self.map_scale = in_sz // dummy_out.shape[-1]
        out = self.encoder(torch.zeros(1, 3, 1, 320, 320).to(next(self.encoder.parameters()).device))
        # scale = out[1].shape[-2:]

    def make_head(self, depth=1):
        head = []
        if depth >= 0:
            dims = [self.enc_hid_dim] + [self.enc_hid_dim] * depth + [128]
            for d1, d2 in zip(dims, dims[1:]):
                h = nn.Linear(d1, d2)
                head += [h, nn.ReLU()]
            head = head[:-1]

        return nn.Sequential(*head)

    def zeroout_diag(self, A, zero=0):
        mask = (
            (torch.eye(A.shape[-1]).unsqueeze(0).repeat(A.shape[0], 1, 1).bool() < 1)
            .float()
            .cuda()
            )
        
        return A * mask

    def affinity(self, x1, x2):
        in_t_dim = x1.ndim
        if in_t_dim < 4:  # add in time dimension if not there
            x1, x2 = x1.unsqueeze(-2), x2.unsqueeze(-2)

        A = torch.einsum("bctn,bctm->btnm", x1, x2)
        # if self.restrict is not None:
        #     A = self.restrict(A)

        return A.squeeze(1) if in_t_dim < 4 else A

    def stoch_mat(self, A, zero_diagonal=False, do_dropout=True, do_sinkhorn=False):
        """Affinity -> Stochastic Matrix"""

        if zero_diagonal:
            A = self.zeroout_diag(A)

        if do_dropout and self.edgedrop_rate > 0:
            A[torch.rand_like(A) < self.edgedrop_rate] = -1e20

        if do_sinkhorn:
            return utils.sinkhorn_knopp((A / self.temperature).exp(), 
                                        tol=0.01, 
                                        max_iter=100, 
                                        verbose=False)

        return F.softmax(A / self.temperature, dim=-1)

    def pixels_to_nodes(self, x):
        """
        pixel maps -> node embeddings
        Handles cases where input is a list of patches of images (N>1), or list of whole images (N=1)

        Inputs:
            -- 'x' (B x N x C x T x h x w), batch of images
        Outputs:
            -- 'feats' (B x C x T x N), node embeddings
            -- 'maps'  (B x N x C x T x H x W), node feature maps
        """
        B, N, C, T, h, w = x.shape
        maps = self.encoder(x.flatten(0, 1))
        H, W = maps.shape[-2:]

        if self.featdrop_rate > 0:
            maps = self.featdrop(maps)

        if N == 1:  # flatten single image's feature map to get node feature 'maps'
            maps = maps.permute(0, -2, -1, 1, 2).contiguous()
            maps = maps.view(-1, *maps.shape[3:])[..., None, None]
            N, H, W = maps.shape[0] // B, 1, 1

        # compute node embeddings by spatially pooling node feature maps
        feats = maps.mean(-1).mean(-1)
        feats = self.selfsim_fc(feats.transpose(-1, -2)).transpose(-1, -2)
        feats = F.normalize(feats, p=2, dim=1)

        feats = feats.view(B, N, feats.shape[1], T).permute(0, 2, 3, 1)
        maps = maps.view(B, N, *maps.shape[1:])

        return feats, maps

    def extract_sp_feat(self, video, maps, superpixel_mask, max_sp_num):
        """
        video has shape of c, T, h, w
        maps has shape of C, T, H, W
        superpixel_mask has shape of T, h, w
        """

        c, T, h, w = video.shape
        C, T, H, W = maps.shape

        final_feats = []
        final_segment = []

        for t in range(T):
            img_map = maps[:, t].permute(1, 2, 0) # after .permute(): (H, W, C) ; (32, 32, 512)
            segments = superpixel_mask[t] # (h, w) ; (256, 256)
            
            # Tensor of masks for all superpixels; shape: (num_sp, h, w) ; (~50, 256, 256)

            # NB Consider using torch.Tensor.scatter to perform this operation; i.e. sp_mask -> sp
            superpixels = (segments == torch.unique(segments)[:, None, None].expand(-1, h, w)).int()

            ####################################################################################################
            # Follow this route to parallelisation of the whole image_to_node function, i.e. pad `superpixels`; 
            # formerly called `sp_tensor`
            # 
            # By padding the superpixels themselves, we allow the overall output to maintain a "n_superpixels"-
            # dimension (`max_sp_num`), which gets around the need for unvectorised operations. 
            # 
            # 
            # Consider using tensor.scatter (out-of-place) to perform the operation above going from the 
            # superpixel mask to `superpixels`
            # See: https://pytorch.org/docs/stable/generated/torch.Tensor.scatter_.html#torch.Tensor.scatter_ 
            #
            # This will need to be followed by a pad of the form shown immediately below this note. 
            ####################################################################################################
            
            superpixels = F.pad(superpixels, (0,)*5 + (max_sp_num - superpixels.shape[0],))

            # Compute receptive fields relative to each superpixel mask; 
            # shape (num_windows, num_windows, num_sp, window_size, window_size); (32, 32, ~50, 8, 8)
            out = view_as_windows(superpixels, (superpixels.shape[0], h//H, w//W), step=h//H).squeeze(0)
            # Extract features weight as normalized interesction of sp mask and receptive field of each feature
            # size of superpixels for each receptive field; shape (num_windows, num_windows, num_sp); (32, 32, ~50)
            ww_not_norm = out.sum(-1).sum(-1)
            # Size of each superpixel; shape: num_sp; (~50)
            sp_size = superpixels.sum(-1).sum(-1)
            ww_norm = ww_not_norm / sp_size
            # Expand weights and feature maps; shape: (num_windows, num_windows, C, num_sp); (32, 32, 512, ~50)
            ww_norm_expand = ww_norm.unsqueeze(2).repeat(1, 1, C, 1)
            # shape: (num_feat, num_feat, C, num_sp) = (32, 32, 512, ~50)
            img_map_expand = img_map.unsqueeze(-1).repeat(1, 1, 1, ww_norm_expand.shape[-1])
            # NOTE num_windows and num_feat are equal
            # We repeat weights for each feature channel and feat for each superpixel because they are independent
            # Weighted mean of the features
            oo = ww_norm_expand * img_map_expand
            feats = oo.sum(0).sum(0).permute(1, 0)
            final_feats.append(feats)

        return final_feats, final_segment # final_segment appears to just be an empty list returned by extract_sp_feat

    def image_to_nodes(self, x, superpixel_mask, max_sp_num):
        """Inputs:
            -- 'x' (B x C x T x h x w), batch of images
        Outputs:
            -- 'feats' (B x C x T x N), node embeddings
            -- 'maps'  (B x C x T x H x W), node feature maps
        """
        B, T, c, h, w = x.shape
        x = x.permute(0, 2, 1, 3, 4)  # new shape after permute: B, c, T, h, w
        maps = self.encoder(x)
        B, C, T, H, W = maps.shape
        N = max_sp_num

        if self.featdrop_rate > 0:
            maps = self.featdrop(maps)

        ff_list = []
        seg_list = []

        for b in range(B):
            # ff, seg = self.extract_sp_feat(x[b], maps[b], superpixel_mask[b, :, 0, :, :])
            ff, _ff, seg = self.extract_sp_feat(x[b], maps[b], superpixel_mask[b, :, 0, :, :], max_sp_num) # prototyping

            ff_list.append(ff)
            seg_list.append(seg)

        ff_tensor = torch.empty((0, T, max_sp_num, C), requires_grad=True, device="cuda")
        for ff in ff_list:
            ff_time_tensor = torch.empty((0, max_sp_num, C), requires_grad=True, device="cuda")
            for sp_feats in ff:
                temp_sp_feats = F.pad(sp_feats,
                                      pad=(0, 0, 0, max_sp_num - sp_feats.shape[0]),
                                      mode="constant",).unsqueeze(0)
                ff_time_tensor = torch.cat((ff_time_tensor, temp_sp_feats), dim=0)
            ff_tensor = torch.cat((ff_tensor, ff_time_tensor.unsqueeze(0)), dim=0)

        # compute frame embeddings by spatially pooling frame feature maps
        # shape (B, T, SP, C) -> (B, SP, C, T)
        ff_tensor = ff_tensor.permute(0, 2, 3, 1)
        ff_tensor = self.selfsim_fc(ff_tensor.transpose(-1, -2)).transpose(-1, -2)
        ff_tensor = F.normalize(ff_tensor, p=2, dim=2)
        ff_tensor = ff_tensor.permute(0, 2, 3, 1)  # B, C, T, SP

        return ff_tensor, seg_list

    def forward(self, x, superpixel_mask, max_sp_num, just_feats=False):
        """
        Input is B x T x N*C x H x W, where either
           N>1 -> list of patches of images
           N=1 -> list of images
        """
        B, T, C, H, W = x.shape

        #################################################################
        # Image/Pixels to Nodes
        #################################################################

        q = None # Looks like a debugging vestige; TODO Remove

        if superpixel_mask is None:
            # use patches
            _N, C = C // 3, 3
            x = x.transpose(1, 2).view(B, _N, C, T, H, W)
            q, mm = self.pixels_to_nodes(x)
        else:
            # compute superpixels masks if not loaded
            q, mm = self.image_to_nodes(x, superpixel_mask, max_sp_num)

        assert q is not None

        B, C, T, N = q.shape

        if just_feats:
            h, w = np.ceil(np.array(x.shape[-2:]) / self.map_scale).astype(np.int)
            return (q, mm) if _N > 1 else (q, q.view(*q.shape[:-1], h, w))

        #################################################################
        # Compute walks
        #################################################################
        walks = dict()

        As = self.affinity(q[:, :, :-1], q[:, :, 1:])

        A12s = [self.stoch_mat(As[:, i], do_dropout=True) for i in range(T - 1)]

        # Palindromes
        if not self.sk_targets:
            A21s = [self.stoch_mat(As[:, i].transpose(-1, -2), do_dropout=True) for i in range(T - 1)]
            AAs = []
            for i in list(range(1, len(A12s))):
                g = A12s[: i + 1] + A21s[: i + 1][::-1]
                aar = aal = g[0]
                for _a in g[1:]:
                    aar, aal = aar @ _a, _a @ aal

                AAs.append((f"l{i}", aal) if self.flip else (f"r{i}", aar))

            for i, aa in AAs:
                walks[f"cyc {i}"] = [aa, self.xent_targets(aa)]

        # Sinkhorn-Knopp Target (experimental)
        else:
            # TODO A is not defined according to my linter; 
            #      not even in the original videowalk code
            a12, at = A12s[0], self.stoch_mat(A[:, 0], do_dropout=False, do_sinkhorn=True)
            for i in range(1, len(A12s)):
                a12 = a12 @ A12s[i]
                at = self.stoch_mat(As[:, i], do_dropout=False, do_sinkhorn=True) @ at
                with torch.no_grad():
                    targets = (
                        utils.sinkhorn_knopp(at, tol=0.001, max_iter=10, verbose=False)
                        .argmax(-1)
                        .flatten()
                    )
                walks[f"sk {i}"] = [a12, targets]

        #################################################################
        # Compute loss
        #################################################################
        xents = [torch.tensor([0.0]).to(self.args.device)]
        diags = dict()

        for name, (A, target) in walks.items():
            logits = torch.log(A + EPS).flatten(0, -2)
            loss = self.xent(logits, target).mean()
            acc = (torch.argmax(logits, dim=-1) == target).float().mean()
            diags.update({f"{H} xent {name}": loss.detach(), 
                          f"{H} acc {name}": acc})
            xents += [loss]

        #################################################################
        # Visualizations
        #################################################################
        if (np.random.random() < 0.02) and (self.vis is not None) and False:
            with torch.no_grad():
                self.visualize_frame_pair(x, q, mm)
                if _N > 1:  # and False:
                    self.visualize_patches(x, q)

        loss = sum(xents) / max(1, len(xents) - 1)

        return q, loss, diags

    def xent_targets(self, A):
        B, N = A.shape[:2]
        key = "%s:%sx%s" % (str(A.device), B, N)

        if key not in self._xent_targets:
            I = torch.arange(A.shape[-1])[None].repeat(B, 1)
            self._xent_targets[key] = I.view(-1).to(A.device)

        return self._xent_targets[key]

    def visualize_patches(self, x, q):
        # all patches
        all_x = x.permute(0, 3, 1, 2, 4, 5)
        all_x = all_x.reshape(-1, *all_x.shape[-3:])
        all_f = q.permute(0, 2, 3, 1).reshape(-1, q.shape[1])
        all_f = all_f.reshape(-1, *all_f.shape[-1:])
        all_A = torch.einsum("ij,kj->ik", all_f, all_f)
        utils.visualize.nn_patches(self.vis.vis, all_x, all_A[None])

    def visualize_frame_pair(self, x, q, mm):
        t1, t2 = np.random.randint(0, q.shape[-2], (2))
        f1, f2 = q[:, :, t1], q[:, :, t2]

        A = self.affinity(f1, f2)
        A1, A2 = self.stoch_mat(A, False, False), self.stoch_mat(A.transpose(-1, -2), False, False)
        AA = A1 @ A2
        xent_loss = self.xent(torch.log(AA + EPS).flatten(0, -2), self.xent_targets(AA))

        utils.visualize.frame_pair(x, q, mm, t1, t2, A, AA, xent_loss, self.vis.vis)
