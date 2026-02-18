"""
python-for-android recipe for cross-compiling llama.cpp as libllama.so
and installing the llama-cpp-python ctypes bindings.

Usage: Referenced via p4a.local_recipes in buildozer.spec
"""

from pythonforandroid.recipe import Recipe
from pythonforandroid.util import current_directory
from os.path import join, exists
import os
import shutil
import subprocess
import urllib.request
import tarfile

LLAMA_CPP_TAG = "b4722"
LLAMA_CPP_URL = (
    f"https://github.com/ggerganov/llama.cpp/archive/"
    f"refs/tags/{LLAMA_CPP_TAG}.tar.gz"
)


class LlamaCppRecipe(Recipe):
    name = 'llamacpp'
    version = '0.3.16'
    url = ('https://github.com/abetlen/llama-cpp-python/archive/'
           'refs/tags/v{version}.tar.gz')
    depends = ['python3']

    def _ensure_llama_cpp_source(self, vendor_dir):
        """Download llama.cpp source into vendor/llama.cpp if missing."""
        if exists(join(vendor_dir, 'CMakeLists.txt')):
            return

        if exists(vendor_dir):
            shutil.rmtree(vendor_dir)

        cache_dir = join(self.ctx.packages_path, 'llamacpp')
        os.makedirs(cache_dir, exist_ok=True)
        tarball = join(cache_dir, f'llama.cpp-{LLAMA_CPP_TAG}.tar.gz')

        if not exists(tarball):
            print(f'[llamacpp] Downloading llama.cpp {LLAMA_CPP_TAG}...')
            urllib.request.urlretrieve(LLAMA_CPP_URL, tarball)

        print('[llamacpp] Extracting llama.cpp source...')
        with tarfile.open(tarball, 'r:gz') as tf:
            tf.extractall(path=os.path.dirname(vendor_dir))

        extracted = join(
            os.path.dirname(vendor_dir),
            f'llama.cpp-{LLAMA_CPP_TAG}')
        os.rename(extracted, vendor_dir)

    def build_arch(self, arch):
        """Cross-compile libllama.so, then copy Python bindings."""
        source_dir = self.get_build_dir(arch.arch)

        # --- Step 1: Ensure llama.cpp source ---
        vendor_dir = join(source_dir, 'vendor', 'llama.cpp')
        self._ensure_llama_cpp_source(vendor_dir)

        # --- Step 2: Cross-compile llama.cpp ---
        build_dir = join(vendor_dir, 'build_android')
        if exists(build_dir):
            shutil.rmtree(build_dir)
        os.makedirs(build_dir)

        ndk = self.ctx.ndk_dir
        toolchain = join(ndk, 'build', 'cmake', 'android.toolchain.cmake')

        cmake_args = [
            'cmake', vendor_dir,
            f'-DCMAKE_TOOLCHAIN_FILE={toolchain}',
            f'-DANDROID_ABI={arch.arch}',
            '-DANDROID_PLATFORM=android-28',
            '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_C_FLAGS=-march=armv8-a',
            '-DCMAKE_CXX_FLAGS=-march=armv8-a',
            '-DGGML_OPENMP=OFF',
            '-DGGML_LLAMAFILE=OFF',
            '-DBUILD_SHARED_LIBS=ON',
            '-DLLAMA_BUILD_TESTS=OFF',
            '-DLLAMA_BUILD_EXAMPLES=OFF',
            '-DLLAMA_BUILD_SERVER=OFF',
        ]

        with current_directory(build_dir):
            subprocess.check_call(cmake_args)
            subprocess.check_call(['cmake', '--build', '.', '-j4'])

        # --- Step 3: Copy all .so libs into APK ---
        libs_dest = self.ctx.get_libs_dir(arch.arch)
        for root, dirs, files in os.walk(build_dir):
            for f in files:
                if f.endswith('.so'):
                    shutil.copy2(join(root, f), join(libs_dest, f))
                    print(f'[llamacpp] Installed {f}')

        # --- Step 4: Copy llama_cpp Python package into site-packages ---
        python_install_dir = join(
            self.ctx.get_python_install_dir(arch.arch),
            'llama_cpp')
        src_pkg = join(source_dir, 'llama_cpp')

        if exists(python_install_dir):
            shutil.rmtree(python_install_dir)
        shutil.copytree(src_pkg, python_install_dir)
        print(f'[llamacpp] Installed Python package to {python_install_dir}')

        # --- Step 5: Patch ctypes decorator to handle missing symbols ---
        # llama-cpp-python v0.3.16 bindings reference symbols not in their
        # pinned llama.cpp. Patch to skip missing symbols instead of crashing.
        ext_path = join(python_install_dir, '_ctypes_extensions.py')
        if exists(ext_path):
            with open(ext_path, 'r') as f:
                code = f.read()
            old = (
                '            if enabled:\n'
                '                func = getattr(lib, name)\n'
            )
            new = (
                '            if enabled:\n'
                '                try:\n'
                '                    func = getattr(lib, name)\n'
                '                except AttributeError:\n'
                '                    return f\n'
            )
            if old in code:
                code = code.replace(old, new)
                with open(ext_path, 'w') as f:
                    f.write(code)
                print('[llamacpp] Patched _ctypes_extensions for missing symbols')


recipe = LlamaCppRecipe()
