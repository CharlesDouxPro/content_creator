# chmod +x start_server.sh
# ./start_server.sh

apt install nvitop
apt install python3
apt update
apt install -y python3.12-full
apt -o Acquire::ForceIPv4=true install -y curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv venv ~/sglang-env --python 3.12
source ~/sglang-env/bin/activate
uv pip install "sglang[diffusion]" --prerelease=allow
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
mv /etc/apt/sources.list.d/cuda.list /root/cuda.list.bak
apt -o Acquire::ForceIPv4=true update
apt -o Acquire::ForceIPv4=true install -y cuda-toolkit-13-0
apt install -y ffmpeg
mkdir -p /scratch/hf
export CUDA_HOME=/usr/local/cuda-13.0
export PATH=$CUDA_HOME/bin:$PATH
export LD_LIBRARY_PATH=$CUDA_HOME/lib64:$LD_LIBRARY_PATH
export HF_HOME=/scratch/hf
echo 'export HF_HOME=/scratch/hf' >> ~/.bashrc
export TERM=xterm-256color
nvcc --version
python -c "import deep_gemm; print('ok')"