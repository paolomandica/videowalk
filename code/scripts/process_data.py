import os
import pdb
import argparse
import subprocess
from datetime import timedelta
from pathlib import Path
from time import time

import torch
from fast_slic import Slic
from torchvision.datasets.folder import make_dataset
from torchvision.datasets.utils import list_dir
from torchvision.datasets.video_utils import VideoClips
from torchvision.io import read_video, write_video
from tqdm import tqdm
from tqdm.auto import tqdm
from joblib import Parallel, delayed


def get_args():
    # parse arguments
    parser = argparse.ArgumentParser(description='Dataset processing')

    # paths
    parser.add_argument(
        '--input-dir', help='path to directory with input data')
    parser.add_argument(
        '--output-dir', default='',
        help='path to directory where to save outputs')

    parser.add_argument('--workers', default=1, type=int, metavar='N',
                        help='number of data processing workers (default: 1)')

    # task to execute
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument('--segment', action='store_true')
    task_group.add_argument('--resize', action='store_true')
    task_group.add_argument('--check-valid', action='store_true')

    args = parser.parse_args()
    return args


def makedirs(dir1, dir2):
    subdirs = [f.name for f in os.scandir(dir1) if f.is_dir()]
    for subdir in subdirs:
        Path(os.path.join(dir2, subdir)).mkdir(exist_ok=True)
    return subdirs


def resize_clip(args, video_path, size=256):
    output_path = video_path.replace(args.input_dir, args.output_dir)
    if os.path.isfile(output_path):
        st_size = Path(video_path).stat().st_size
        if st_size == 0:
            pass
        else:
            return

    size = str(size)+':'+str(size)
    subprocess.call(
        ['ffmpeg', '-y',  # '-hwaccel', 'cuda',
         '-i', video_path, '-vf', 'scale='+size, '-an',
         '-c:v', 'libopenh264', output_path,
         '-hide_banner', '-loglevel', 'error'])


def generate_sp_mask(args, video_path):
    output_path = video_path.replace("train_256", "masks")

    if os.path.isfile(output_path):
        return
    else:
        try:
            video = read_video(video_path)  # shape (T, H, W, C)
            fps = video[2]['video_fps']
        except KeyError as k:
            print("KeyError:", k)
            print("Videopath:", video_path)

        except Exception as e:
            print("Cannot read the video:", e)
            print("Videopath:", video_path)

        video = video[0].permute(3, 0, 1, 2)  # Shape (C, T  H, W)

        slic = Slic(num_components=50, compactness=30)
        sp_tensor_time = []

        for t in range(video.shape[1]):
            img = video[:, t, :, :]
            img = img.permute(1, 2, 0).cpu().numpy()
            img = img.astype(dtype='uint8', order='C')
            segments_slic = slic.iterate(img).astype(dtype='uint8')
            sp_tensor_time.append(torch.from_numpy(segments_slic))

        # torch.save(torch.stack(sp_tensor_time), output_path)
        final_t = torch.stack(sp_tensor_time)
        final_t = final_t.unsqueeze(3).repeat(1, 1, 1, 3)
        write_video(output_path, final_t, fps=fps)


def check_valid(video_list):
    c = 0
    print("\nStarting process to check video validity...\n")
    for video_path in tqdm(video_list):
        size = Path(video_path).stat().st_size
        if size == 0:
            c += 1
    print("\n\nThere are %d empty videos.\n\n" % (c))


def execute(args):
    start = time()

    if args.output_dir != '':
        subdirs = makedirs(args.input_dir, args.output_dir)

    classes = list(sorted(list_dir(args.input_dir)))
    class_to_idx = {classes[i]: i for i in range(len(classes))}
    extensions = ('mp4',)

    samples = make_dataset(args.input_dir, class_to_idx,
                           extensions, is_valid_file=None)

    video_list = [sample[0] for sample in samples]

    if args.check_valid:
        print("\n========= Starting validity check =========\n")
        check_valid(video_list)

    else:
        req = None
        assert args.workers >= 1, "workers should be >= 1"

        if args.segment:
            print("\n========= Starting segmentation process =========\n")
            func = generate_sp_mask
        elif args.resize:
            print("\n========= Starting resizing process =========\n")
            func = resize_clip
            req = "sharedmem"
        else:
            raise "Choose a valid task argument."

        if args.workers == 1:
            # single-process
            for path in tqdm(video_list):
                func(args, path)
        else:
            # multi-process
            Parallel(n_jobs=args.workers, require=req)(delayed(func)(args, path)
                                                       for path in tqdm(video_list))

    end = time()
    tot_time = str(timedelta(seconds=round(end-start)))
    print("\n=========== Completed in %s ===========\n" % (tot_time))


if __name__ == "__main__":
    args = get_args()

    # if the output folder doesn't exists it is automatically created with all the parent folders
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # create superpixels segmentation masks for each videoclip
    execute(args)
