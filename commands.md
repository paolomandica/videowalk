# Terminal Commands for Video Contrastive [Sapienza]

### Check Containers Run from an Image

```sh
docker ps -a -f ancestor=paolomandica/sapienza-video-contrastive `# ancestor identifies the image`
```

See also docs for `docker ps` listing containers <https://docs.docker.com/engine/reference/commandline/ps/>. 

### Docker Container on DGX

NB Before running, ensure the Docker is using the appropriate context (i.e. DGXContext locally). The context can be set with:

`docker context use DGXContext`

```sh
docker run -it \
--user "$(id -u):$(id -g)" \
--gpus '"device=5"' \
--shm-size 4G \
-v /raid/data/francolu:/data_volume \
-p 8093:8093 \
paolomandica/sapienza-video-contrastive
```

Or run with a specific name: [NAME_OF_CONTAINER]. 

```sh
docker run -it \
--user "$(id -u):$(id -g)" \
--gpus '"device=2"' \
--name "[NAME_OF_CONTAINER]" \
--shm-size 4G \
-v /raid/data/francolu:/data_volume \
-p 8092:8092 \
paolomandica/sapienza-video-contrastive
```

Note that ports 8092-8096 are unassigned, so can be allocated. See <https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xhtml?&page=109>. 

**Explanation / Commented Version**

```sh
docker run -it `# run command, keep STDIN open for interactive mode, attach stdin as a tty` \
--user "$(id -u):$(id -g)" `# Username or UID (format: <name|uid>[:<group|gid>]); uses command substitution` \
--gpus '"device=5"' `# GPU devices to add to the container` \
--name "francolu_contrastive_TS" `# assign name to the container` \
--shm-size 4G `# size of /dev/shm` \
-v /raid/data/francolu:/data_volume `# bind mount a volume` \
-p 8096:8096  `# publish container's port(s) to the host` \
paolomandica/sapienza-video-contrastive `# Docker Image to Run`
```

### Pull Down Project from GitHub

Clone repo (inc. PAT), `cd` into it, checkout `ts` branch and `cd` into the code subdirectory. 

```sh
git clone https://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@github.com/anilkeshwani/video-contrastive.git && \
cd video-contrastive && \
git checkout ts && \
cd code
```

NB The above OAuth token has been revoked for security; replace the above with a valid one for access with OAuth. 

### Installs

Some installs may be required in the container. For example:

```sh
pip install visdom wandb av
pip install fast_slic # for superpixels
```

These install into `/home/francolu/.local/bin`. Optionally add this to the `PATH` via

```sh
export PATH="$HOME/.local/bin:$PATH"

# or
export PATH="/home/francolu/.local/bin:$PATH"

# or
export PATH="/any/arbitrary/path/to/binaries/or/else/here:$PATH"
```

Check the PATH environment variable has been correctly updated with `echo $PATH$`. It should have the new path added followed by a colon, `:`, and then the rest of the path as existed previously since the PATH is a colon-separated string. 

### Server Utilisation

Watch (and update) GPU usage statistics on the server. 

NB `watch` with `-d` flag highlights diffs across prints, `-n` sets refresh rate. 

```sh
watch -d -n 0.5 nvidia-smi
```

## Attaching a Container to VSCode

You can attach VSCode to a running container. In this case, settings such as the `workspaceFolder` can be set as desired to e.g. attach the VSCode Explorer pane to the correct directory as the project root. 

Example of Sapienza Video Contrastive JSON:

```json
{
	"workspaceFolder": "/home/francolu"
}
```

Example generic `devcontainer.json`

```json
{
  // Default path to open when attaching to a new container.
  "workspaceFolder": "/path/to/code/in/container/here",

  // An array of extension IDs that specify the extensions to
  // install inside the container when you first attach to it.
  "extensions": ["dbaeumer.vscode-eslint"],

  // Any *default* container specific VS Code settings
  "settings": {
    "terminal.integrated.shell.linux": "/bin/bash"
  },

  // An array port numbers to forward
  "forwardPorts": [8000],

  // Container user VS Code should use when connecting
  "remoteUser": "vscode",

  // Set environment variables for VS Code and sub-processes
  "remoteEnv": { "MY_VARIABLE": "some-value" }
}
```

See <https://code.visualstudio.com/docs/remote/attach-container#_attached-container-configuration-files> for details. 

In particular very usefully how to edit dev container configuration files if something goes wrong:

> Tip: If something is wrong with your configuration, you can also edit it when not attached to the container by selecting Remote-Containers: Open Attached Container Configuration File... from the Command Palette (F1) and then picking the image / container name from the presented list.

NB The dev container configuration files on my machine are located in `C:\Users\anilk\AppData\Roaming\Code\User\globalStorage\ms-vscode-remote.remote-containers\imageConfigs`. 

## Logging with Weights & Biases (wandb)

Logging with Weights & Biases is done by logging in on a host machine, be it a local machine, the DGX server or any other server. 

After installing `wandb`, for example via `pip install wandb` as above, one can access tools via the command-line interface. For details, see `wandb --help`, which summarily lists the following commands

```
Commands:
  agent         Run the W&B agent
  artifact      Commands for interacting with artifacts
  controller    Run the W&B local sweep controller
  disabled      Disable W&B.
  docker        W&B docker lets you run your code in a docker image...
  docker-run    Simple wrapper for `docker run` which adds WANDB_API_KEY...
  enabled       Enable W&B.
  init          Configure a directory with Weights & Biases
  launch        Launch or queue a job from a uri (Experimental).
  launch-agent  Run a W&B launch agent (Experimental)
  local         Launch local W&B container (Experimental)
  login         Login to Weights & Biases
  offline       Disable W&B sync
  online        Enable W&B sync
  pull          Pull files from Weights & Biases
  restore       Restore code, config and docker state for a run
  status        Show configuration settings
  sweep         Create a sweep
  sync          Upload an offline training directory to W&B
  verify        Verify your local instance
```

Login can be done with

If already logged in, this will prompt display of the current logged in user, for example

```
wandb: Currently logged in as: anilkeshwani (use `wandb login --relogin` to force relogin)
```

If directly using wandb during training without prior authentication, a prompt will allow you to choose from

```
wandb: (1) Create a W&B account
wandb: (2) Use an existing W&B account
wandb: (3) Don't visualize my results
```

Upon logging in by pasting an API key, this will be appended to your netrc file, for example

```
Appending key for api.wandb.ai to your netrc file: /home/francolu/.netrc
```

Use `cat /home/francolu/.netrc` or generically `cat /path/to/.netrc` to view the contents, which initially are like

```
machine api.wandb.ai
  login user
  password [REDACTED; a hash-like string, not your plaintext password]
```

## screen

Linux `screen` offers the possibility of running virtual terminals inside a multiplexer and brings the advantage that remote session disconnects (e.g. SSH disconnects) do not stop the process running inside the virtual terminals. 

You start a screen session via `screen` and can "detach" from the session via `ctrl + a, d`. Then return to the detached session with `screen -r` or `screen -r [session_id]` when running multiple screens. NB list the running screen sessions with `screen -ls`. 

Create a new terminal (e.g. bash) with `ctrl + a, c` and list the open terminals in a screen with `ctrl + a, "`. 

More details are available at <https://linuxize.com/post/how-to-use-linux-screen/>. 

For more shortcut keys, see <http://www.pixelbeat.org/lkdb/screen.html> and consider also [tmux](https://github.com/tmux/tmux/wiki) as an alternative multiplexer. 
