# A stand-in for a RunPod server in the remote render rehearsal (tests/remote/rehearsal.py): x86-64 Ubuntu with sshd,
# Python and curl, and a /start.sh that does what RunPod's images do with PUBLIC_KEY. No GPU and no Blender: the
# engine's bootstrap installs Blender the way it does on a real server.
FROM --platform=linux/amd64 ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server python3 python3-pip curl rsync xz-utils \
    ffmpeg libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 libxkbcommon0 libsm6 libegl1 >/dev/null && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh
RUN printf '#!/bin/bash\necho "$PUBLIC_KEY" > /root/.ssh/authorized_keys\nchmod 600 /root/.ssh/authorized_keys\nenv | grep -E "^(RUNPOD_|STUDIO_)" | sed "s/^/export /" > /etc/profile.d/runpod.sh\n/usr/sbin/sshd\n' > /start.sh && chmod +x /start.sh
EXPOSE 22
