#!/usr/bin/env python3
"""Install a pinned whisper.cpp and the local hotkey runtime without desktop changes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile


WHISPER_VERSION = "v1.8.3"
WHISPER_COMMIT = "2eeeba56e9edd762b4b38467bab96c2517163158"
WHISPER_URL = "https://github.com/ggml-org/whisper.cpp.git"
# Upstream models/README.md at WHISPER_COMMIT; SHA1 detects incomplete/corrupt downloads.
MODEL_SHA1 = {
    "tiny": "bd577a113a864445d4c299885e0cb97d4ba92b5f",
    "tiny.en": "c78c86eb1a8faa21b369bcd33207cc90d64ae9df",
    "base": "465707469ff3a37a2b9b8d8f89f2f99de7299dac",
    "base.en": "137c40403d78fd54d454da0f9bd998f78703390c",
    "small": "55356645c2b361a969dfd0ef2c5a50d530afd8d5",
    "small.en": "db8a495a91d927739e50b3fc1cc4c6b8f6c2d022",
    "medium": "fd9727b6e1217c2f614f9b698455c4ffd82463b4",
    "medium.en": "8c30f0e44ce9560643ebd10bbe50cd20eafd3723",
    "large-v1": "b1caaf735c4cc1429223d5a74f0f4d0b9b59a299",
    "large-v2": "0f4c8e34f21cf1a914c59d8b3ce882345ad349d6",
    "large-v3": "ad82bf6a9043ceed055076d0fd39f5f186ff8062",
    "large-v3-turbo": "4af2b29d7ec73d781377bfd1758ca957a807e941",
}
MODELS = tuple(MODEL_SHA1)


def default_prefix(system, env):
    home = Path(env.get("HOME", str(Path.home())))
    if system == "Darwin":
        return home / "Library" / "Application Support" / "whisper-hotkey"
    data_home = env.get("XDG_DATA_HOME")
    # The XDG specification requires absolute paths.
    if not data_home or not Path(data_home).is_absolute():
        data_home = home / ".local" / "share"
    return Path(data_home) / "whisper-hotkey"


def package_manager(system, requested="auto", which=None):
    which = which or shutil.which
    supported = {"Linux": ("apt-get", "dnf", "pacman", "zypper"), "Darwin": ("brew",)}
    if system not in supported:
        raise RuntimeError("Supported platforms are Linux and macOS; native Windows is not supported.")
    candidates = supported[system]
    if requested != "auto":
        if requested not in candidates:
            raise RuntimeError(f"Package manager {requested} is not supported on {system}.")
        candidates = (requested,)
    for candidate in candidates:
        if which(candidate):
            return candidate
    if system == "Darwin":
        raise RuntimeError("Homebrew was not found. Install Homebrew, or provide dependencies and use --skip-deps.")
    raise RuntimeError("No supported package manager found (apt-get, dnf, pacman, zypper). Provide dependencies and use --skip-deps.")


def dependency_commands(manager, env, is_root=False):
    clipboard = "wl-clipboard" if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland" else "xclip"
    prefix = [] if is_root or manager == "brew" else ["sudo"]
    common = ["cmake", "git", "curl"]
    packages = {
        "apt-get": ["build-essential", *common, "alsa-utils", clipboard, "libnotify-bin"],
        "dnf": ["gcc-c++", "make", *common, "alsa-utils", clipboard, "libnotify"],
        "pacman": ["base-devel", *common, "alsa-utils", clipboard, "libnotify"],
        "zypper": ["gcc-c++", "make", *common, "alsa-utils", clipboard, "libnotify-tools"],
        "brew": [*common, "sox"],
    }
    options = {
        "apt-get": ["apt-get", "install", "-y"],
        "dnf": ["dnf", "install", "-y"],
        "pacman": ["pacman", "-S", "--needed", "--noconfirm"],
        "zypper": ["zypper", "--non-interactive", "install"],
        "brew": ["brew", "install"],
    }
    commands = [prefix + options[manager] + packages[manager]]
    if manager == "apt-get":
        commands.insert(0, prefix + ["apt-get", "update"])
    return commands


class Installer:
    def __init__(self, args, system=None, env=None, source_dir=None):
        self.args = args
        self.system = system or platform.system()
        self.env = os.environ if env is None else env
        self.source_dir = Path(source_dir) if source_dir else Path(__file__).resolve().parent.parent
        self.prefix = Path(args.prefix).expanduser().absolute() if args.prefix else default_prefix(self.system, self.env).absolute()
        self.checkout = self.prefix / "whisper.cpp"
        self.binary = self.checkout / "build" / "bin" / "whisper-cli"
        self.model = self.checkout / "models" / f"ggml-{args.model}.bin"

    def command(self, command, cwd=None):
        command = [str(arg) for arg in command]
        where = f" (in {shlex.quote(str(cwd))})" if cwd else ""
        print(f"  $ {shlex.join(command)}{where}", flush=True)
        if not self.args.dry_run:
            subprocess.run(command, cwd=cwd, check=True)

    def verify_checkout(self):
        if self.checkout.is_symlink():
            raise RuntimeError(f"Refusing symlink at {self.checkout}; choose a separate --prefix.")
        if not (self.checkout / ".git").is_dir():
            raise RuntimeError(f"{self.checkout} is not an installer checkout; choose a separate --prefix.")
        head = subprocess.run(["git", "-C", str(self.checkout), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
        if head != WHISPER_COMMIT:
            raise RuntimeError(f"Existing whisper.cpp is not pinned to {WHISPER_VERSION} ({WHISPER_COMMIT}). It was left unchanged; choose a separate --prefix.")
        changed = subprocess.run(["git", "--no-optional-locks", "-C", str(self.checkout), "status", "--porcelain", "--untracked-files=normal"], check=True, text=True, capture_output=True).stdout
        if changed:
            raise RuntimeError("Existing whisper.cpp has local changes. They were left unchanged; choose a separate --prefix.")

    def check_dependencies(self):
        required = ["git", "cmake", "make", "curl", "bash"]
        required += ["rec", "pbcopy"] if self.system == "Darwin" else []
        if self.system == "Linux":
            wayland = self.env.get("WAYLAND_DISPLAY") or self.env.get("XDG_SESSION_TYPE") == "wayland"
            alternatives = ("wl-copy",) if wayland else ("xclip", "xsel")
            if not any(shutil.which(tool) for tool in alternatives):
                required.append(alternatives[0])
        missing = [tool for tool in required if not shutil.which(tool)]
        if self.system == "Linux" and not any(shutil.which(tool) for tool in ("pw-record", "parecord", "arecord")):
            missing.append("recording tool (pw-record, parecord, or arecord)")
        if not any(shutil.which(tool) for tool in ("c++", "g++", "clang++")):
            missing.append("C++ compiler (c++, g++, or clang++)")
        if missing:
            raise RuntimeError("Missing dependencies: " + ", ".join(missing) + ". Install these tools and retry. On macOS, install Xcode Command Line Tools as well as Homebrew dependencies.")

    def validate_paths(self):
        directories = [self.prefix, self.checkout, self.checkout / "models", self.checkout / "build"]
        files = [self.prefix / name for name in ("config.json", "whisper_hotkey.py", "whisper-toggle.sh")] + [self.model]
        for path in directories + files:
            if path.is_symlink():
                raise RuntimeError(f"Refusing symlink at {path}; choose a separate --prefix.")
            if path.exists() and not (path.is_dir() if path in directories else path.is_file()):
                kind = "directory" if path in directories else "regular file"
                raise RuntimeError(f"Expected {kind} at {path}; choose a separate --prefix.")
        for parent in self.prefix.parents:
            if parent.exists() and not parent.is_dir():
                raise RuntimeError(f"Expected directory at {parent}; choose a separate --prefix.")

    def read_config(self):
        path = self.prefix / "config.json"
        if not path.exists():
            return {}
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise RuntimeError(f"Cannot read existing configuration {path}: {exc}") from exc
        if not isinstance(config, dict):
            raise RuntimeError(f"Existing configuration {path} must be a JSON object.")
        return config

    def verify_model(self, path, *, downloaded=False):
        digest = hashlib.sha1(usedforsecurity=False)
        with path.open("rb") as model:
            for chunk in iter(lambda: model.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA1[self.args.model]:
            if downloaded:
                raise RuntimeError(f"Downloaded model checksum mismatch for {self.model.name}. Retry installation to download a fresh copy.")
            raise RuntimeError(f"Model checksum mismatch at {path}. The file was left unchanged; remove it and retry, or choose a separate --prefix.")

    def download_model(self):
        self.validate_paths()
        if self.model.exists():
            self.verify_model(self.model)
            return
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{self.model.name}"
        temporary = self.model.with_suffix(".bin.download")
        if not self.args.dry_run:
            with tempfile.NamedTemporaryFile(prefix=self.model.name + ".", suffix=".download", dir=self.model.parent, delete=False) as output:
                temporary = Path(output.name)
        try:
            self.command([
                "curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "30",
                "--speed-limit", "1", "--speed-time", "60", "--output", temporary, url,
            ])
            if self.args.dry_run:
                print(f"  Verify model SHA1 {MODEL_SHA1[self.args.model]} before publishing {self.model}.")
            else:
                self.verify_model(temporary, downloaded=True)
                os.replace(temporary, self.model)
        finally:
            if not self.args.dry_run:
                temporary.unlink(missing_ok=True)

    def write_runtime(self):
        self.validate_paths()
        config_path = self.prefix / "config.json"
        config = {
            "whisper_bin": str(self.binary), "model_path": str(self.model),
            "backend": "auto", "clipboard": "auto", "language": "auto", "threads": 4,
        }
        # Keep user choices, while moving the engine/model to this installation.
        config.update(self.read_config())
        config.update(whisper_bin=str(self.binary), model_path=str(self.model))
        runtime = self.prefix / "whisper_hotkey.py"
        launcher = self.prefix / "whisper-toggle.sh"
        python = str(Path(sys.executable).resolve())
        # Desktop shortcuts often omit Homebrew or user-installed tools from PATH.
        tools = ("rec", "arecord", "parecord", "pw-record", "pbcopy", "wl-copy", "xclip", "xsel", "notify-send", "osascript")
        tool_dirs = sorted({str(Path(found).absolute().parent) for tool in tools if (found := shutil.which(tool))})
        path_setup = f"PATH={shlex.quote(':'.join(tool_dirs))}:\"${{PATH:-/usr/bin:/bin}}\"\nexport PATH\n" if tool_dirs else ""
        # Set the shell default separately so spaces and quotes stay literal.
        launcher_text = (
            "#!/bin/sh\nset -eu\n" + path_setup +
            "if [ -z \"${WHISPER_HOTKEY_CONFIG:-}\" ]; then\n"
            f"    WHISPER_HOTKEY_CONFIG={shlex.quote(str(config_path))}\n"
            "fi\nexport WHISPER_HOTKEY_CONFIG\n"
            f"exec {shlex.quote(python)} {shlex.quote(str(runtime))} \"$@\"\n"
        )
        shutil.copyfile(self.source_dir / "scripts" / "whisper_hotkey.py", runtime)
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        config_path.chmod(0o600)
        launcher.write_text(launcher_text)
        launcher.chmod(0o755)

    def install(self):
        if self.system not in ("Linux", "Darwin"):
            raise RuntimeError("Supported platforms are Linux and macOS; native Windows is not supported.")
        if "\n" in str(self.prefix) or "\r" in str(self.prefix):
            raise RuntimeError("Installation prefix must not contain newline characters.")
        print(f"{'Dry run: ' if self.args.dry_run else ''}install in {self.prefix}")
        # Validate before package-manager mutations or overwriting any user files.
        self.validate_paths()
        self.read_config()
        if self.model.exists():
            self.verify_model(self.model)
        if self.checkout.exists() or self.checkout.is_symlink():
            self.verify_checkout()
        if not self.args.dry_run and not (self.source_dir / "scripts" / "whisper_hotkey.py").is_file():
            raise RuntimeError("Runtime source scripts/whisper_hotkey.py is missing; use a complete project checkout.")
        if not self.args.skip_deps:
            manager = package_manager(self.system, self.args.package_manager)
            if manager != "brew" and os.geteuid() != 0 and not shutil.which("sudo"):
                raise RuntimeError("sudo was not found. Install dependencies as an administrator, then retry with --skip-deps.")
            for command in dependency_commands(manager, self.env, is_root=os.geteuid() == 0):
                self.command(command)
        if not self.args.dry_run:
            self.check_dependencies()
            self.prefix.mkdir(parents=True, exist_ok=True)
        else:
            print("  Check build, recording, and clipboard dependencies before building.")
        if not self.checkout.exists():
            self.command(["git", "clone", "--branch", WHISPER_VERSION, "--depth", "1", WHISPER_URL, self.checkout])
            if not self.args.dry_run:
                self.verify_checkout()
            else:
                print(f"  Verify clean checkout at {WHISPER_COMMIT}.")
        self.command([
            "cmake", "-S", self.checkout, "-B", self.checkout / "build",
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF", "-DGGML_NATIVE=OFF", "-DGGML_METAL=OFF",
            "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_EXAMPLES=ON", "-DWHISPER_BUILD_SERVER=OFF",
        ])
        self.command(["cmake", "--build", self.checkout / "build", "--config", "Release", "--target", "whisper-cli", "--parallel", str(max(1, os.cpu_count() or 1))])
        self.download_model()
        self.command([self.binary, "--help"])
        if not self.args.dry_run:
            self.write_runtime()
        else:
            print(f"  Write runtime, launcher, and JSON configuration under {self.prefix}.")
        print(f"{'Planned shortcut command' if self.args.dry_run else 'Installation complete. Shortcut command'}:")
        print(f"  {shlex.quote(str(self.prefix / 'whisper-toggle.sh'))}")
        print("Assign this command to F9 (or another key) in your desktop keyboard settings.")
        print("Run the command with 'doctor' to check configuration. Microphone access is requested when you start recording.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", help="Installation directory (default: user application data directory)")
    parser.add_argument("--model", choices=MODELS, default="base", help="Whisper model to download (default: base)")
    parser.add_argument("--skip-deps", action="store_true", help="Use already-installed dependencies")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without installing or creating files")
    parser.add_argument("--package-manager", choices=("auto", "apt-get", "dnf", "pacman", "zypper", "brew"), default="auto")
    return parser.parse_args(argv)


def main(argv=None):
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.", file=sys.stderr)
        return 1
    try:
        Installer(parse_args(argv)).install()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
