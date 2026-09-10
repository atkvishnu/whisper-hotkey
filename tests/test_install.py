"""Installer plans and file-boundary tests; no package manager or network access."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("hotkey_install", ROOT / "scripts" / "install.py")
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class DependencyPlans(unittest.TestCase):
    def test_supported_managers_have_native_install_commands(self):
        expected = {
            "apt-get": ["sudo", "apt-get", "install", "-y"],
            "dnf": ["sudo", "dnf", "install", "-y"],
            "pacman": ["sudo", "pacman", "-S", "--needed", "--noconfirm"],
            "zypper": ["sudo", "zypper", "--non-interactive", "install"],
            "brew": ["brew", "install"],
        }
        for manager, start in expected.items():
            with self.subTest(manager=manager):
                command = install.dependency_commands(manager, {})[-1]
                self.assertEqual(command[:len(start)], start)
                self.assertIn("cmake", command)
                self.assertIn("git", command)
                self.assertIn("curl", command)
                self.assertNotIn("-Sy", command)
                self.assertNotIn("upgrade", command)
                self.assertIn("sox" if manager == "brew" else "alsa-utils", command)

    def test_clipboard_selection_and_root_do_not_use_sudo(self):
        for manager in ("apt-get", "dnf", "pacman", "zypper"):
            with self.subTest(manager=manager):
                for env in ({"WAYLAND_DISPLAY": "wayland-0"}, {"XDG_SESSION_TYPE": "wayland"}):
                    command = install.dependency_commands(manager, env, is_root=True)[-1]
                    self.assertIn("wl-clipboard", command)
                    self.assertNotIn("xclip", command)
                    self.assertNotIn("sudo", command)
                self.assertIn("xclip", install.dependency_commands(manager, {})[-1])

    def test_manager_detection_and_override(self):
        for manager in ("apt-get", "dnf", "pacman", "zypper"):
            with self.subTest(manager=manager):
                self.assertEqual(install.package_manager("Linux", which=lambda item: item == manager), manager)
        self.assertEqual(install.package_manager("Darwin", which=lambda item: item == "brew"), "brew")
        self.assertEqual(install.package_manager("Linux", "pacman", which=lambda item: True), "pacman")
        with self.assertRaisesRegex(RuntimeError, "Homebrew was not found"):
            install.package_manager("Darwin", which=lambda item: None)
        with self.assertRaisesRegex(RuntimeError, "No supported package manager"):
            install.package_manager("Linux", which=lambda item: None)
        with self.assertRaisesRegex(RuntimeError, "not supported"):
            install.package_manager("Darwin", "apt-get", which=lambda item: True)

    def test_platform_default_paths(self):
        self.assertEqual(install.default_prefix("Darwin", {"HOME": "/Users/a b"}), Path("/Users/a b/Library/Application Support/whisper-hotkey"))
        self.assertEqual(install.default_prefix("Linux", {"HOME": "/home/test"}), Path("/home/test/.local/share/whisper-hotkey"))
        self.assertEqual(install.default_prefix("Linux", {"HOME": "/home/test", "XDG_DATA_HOME": "/data here"}), Path("/data here/whisper-hotkey"))
        self.assertEqual(install.default_prefix("Linux", {"HOME": "/home/test", "XDG_DATA_HOME": "relative"}), Path("/home/test/.local/share/whisper-hotkey"))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hotkey install '")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "project source"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "scripts" / "whisper_hotkey.py").write_text(
            "import json, os, sys\nprint(json.dumps({'config': os.environ['WHISPER_HOTKEY_CONFIG'], 'args': sys.argv[1:]}))\n"
        )
        self.prefix = self.root / "my install $(touch NEVER)"
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def installer(self, *args, system="Linux", env=None):
        options = install.parse_args(["--prefix", str(self.prefix), *args])
        return install.Installer(options, system=system, env={} if env is None else env, source_dir=self.source)

    def check_dependencies_with(self, *desktop_tools, system="Linux"):
        available = {"git", "cmake", "make", "curl", "bash", "c++", *desktop_tools}
        with patch.object(install.shutil, "which", side_effect=lambda tool: f"/fake/{tool}" if tool in available else None):
            self.installer(system=system).check_dependencies()

    def test_linux_preflight_accepts_only_pipewire_recorder(self):
        self.check_dependencies_with("pw-record", "xclip")

    def test_linux_preflight_accepts_only_pulseaudio_recorder(self):
        self.check_dependencies_with("parecord", "xclip")

    def test_linux_preflight_accepts_only_alsa_recorder(self):
        self.check_dependencies_with("arecord", "xclip")

    def test_linux_preflight_rejects_missing_recorder_and_lists_alternatives(self):
        with self.assertRaises(RuntimeError) as failure:
            self.check_dependencies_with("xclip")
        self.assertIn("recording tool (pw-record, parecord, or arecord)", str(failure.exception))

    def test_macos_preflight_accepts_rec_and_pbcopy_without_linux_recorders(self):
        self.check_dependencies_with("rec", "pbcopy", system="Darwin")

    def test_macos_preflight_requires_rec_even_with_linux_recorders(self):
        with self.assertRaisesRegex(RuntimeError, "Missing dependencies: rec\\."):
            self.check_dependencies_with("pw-record", "parecord", "arecord", "pbcopy", system="Darwin")

    def test_macos_preflight_requires_pbcopy(self):
        with self.assertRaisesRegex(RuntimeError, "Missing dependencies: pbcopy\\."):
            self.check_dependencies_with("rec", "xclip", "wl-copy", system="Darwin")

    def test_dry_run_has_no_files_subprocesses_or_runtime_requirement(self):
        installer = self.installer("--dry-run", "--skip-deps")
        installer.source_dir = self.root / "missing source"
        with patch.object(install.subprocess, "run") as run:
            installer.install()
        run.assert_not_called()
        self.assertFalse(self.prefix.exists())
        plan = self.output.getvalue()
        for text in ("cmake -S", "--target whisper-cli", "-DBUILD_SHARED_LIBS=OFF", "-DGGML_NATIVE=OFF", "-DGGML_METAL=OFF", install.WHISPER_VERSION, install.WHISPER_COMMIT):
            self.assertIn(text, plan)
        self.assertNotIn("make clean", plan)
        self.assertNotIn("git pull", plan)
        self.assertNotIn("sudo", plan)

    def test_each_manager_can_plan_without_mutations(self):
        for manager in ("apt-get", "dnf", "pacman", "zypper", "brew"):
            with self.subTest(manager=manager):
                installer = self.installer("--dry-run", "--package-manager", manager, system="Darwin" if manager == "brew" else "Linux")
                with patch.object(install.shutil, "which", return_value="/fake/tool"), patch.object(install.subprocess, "run") as run:
                    installer.install()
                run.assert_not_called()
                self.assertFalse(self.prefix.exists())

    def test_missing_dependencies_stop_before_clone_and_runtime_write(self):
        installer = self.installer("--skip-deps")
        with patch.object(install.shutil, "which", return_value=None), patch.object(install.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Missing dependencies:.*cmake"):
                installer.install()
        run.assert_not_called()
        self.assertFalse(self.prefix.exists())

    def test_reject_unsupported_platform_even_with_skip_deps(self):
        with self.assertRaisesRegex(RuntimeError, "Windows"):
            self.installer("--skip-deps", "--dry-run", system="Windows").install()
        self.assertFalse(self.prefix.exists())

    def test_existing_unmanaged_directory_is_preserved(self):
        checkout = self.prefix / "whisper.cpp"
        checkout.mkdir(parents=True)
        marker = checkout / "my-source.txt"
        marker.write_text("do not alter")
        with patch.object(install.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "not an installer checkout"):
                self.installer("--dry-run").install()
        run.assert_not_called()
        self.assertEqual(marker.read_text(), "do not alter")

    def test_existing_wrong_revision_or_dirty_checkout_is_rejected_before_packages(self):
        (self.prefix / "whisper.cpp" / ".git").mkdir(parents=True)
        for head, changed, message in (("wrong", "", "not pinned"), (install.WHISPER_COMMIT, " M source.cpp\n", "local changes")):
            commands = []

            def git_read(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, stdout=head + "\n" if "rev-parse" in command else changed)

            with self.subTest(message=message), patch.object(install.subprocess, "run", side_effect=git_read):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.installer().install()
            self.assertTrue(all(command[0] == "git" for command in commands))
            self.assertFalse((self.prefix / "whisper-toggle.sh").exists())

    def test_symlink_checkout_is_rejected(self):
        self.prefix.mkdir()
        (self.prefix / "whisper.cpp").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "Refusing symlink"):
            self.installer("--skip-deps").install()

    def test_corrupt_existing_model_is_preserved_and_rejected_before_mutations(self):
        installer = self.installer("--skip-deps")
        installer.model.parent.mkdir(parents=True)
        installer.model.write_bytes(b"partial model or HTTP error")
        with patch.object(installer, "verify_checkout"), patch.object(installer, "check_dependencies"), patch.object(installer, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                installer.install()
        command.assert_not_called()
        self.assertEqual(installer.model.read_bytes(), b"partial model or HTTP error")
        self.assertFalse((self.prefix / "whisper-toggle.sh").exists())

    def test_unsafe_write_paths_are_rejected_before_package_commands(self):
        for relative in (".", "config.json", "whisper_hotkey.py", "whisper-toggle.sh", "whisper.cpp/models", "whisper.cpp/build", "whisper.cpp/models/ggml-base.bin"):
            with self.subTest(path=relative):
                prefix = self.root / ("unsafe-" + relative.replace("/", "-"))
                destination = prefix / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(self.source if relative in (".", "whisper.cpp/models", "whisper.cpp/build") else self.source / "scripts" / "whisper_hotkey.py")
                installer = self.installer()
                installer.prefix = prefix
                installer.checkout = prefix / "whisper.cpp"
                installer.model = installer.checkout / "models" / "ggml-base.bin"
                with patch.object(installer, "verify_checkout"), patch.object(installer, "command") as command:
                    with self.assertRaisesRegex(RuntimeError, "Refusing symlink"):
                        installer.install()
                command.assert_not_called()

    def test_prefix_file_and_config_directory_are_rejected_before_packages(self):
        for prefix_is_file in (True, False):
            with self.subTest(prefix_is_file=prefix_is_file):
                if prefix_is_file:
                    self.prefix.write_text("keep me")
                else:
                    (self.prefix / "config.json").mkdir(parents=True)
                with patch.object(install.subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "Expected (directory|regular file)"):
                        self.installer().install()
                run.assert_not_called()
                if prefix_is_file:
                    self.assertEqual(self.prefix.read_text(), "keep me")
                    self.prefix.unlink()

    def test_malformed_config_is_rejected_before_package_commands(self):
        self.prefix.mkdir()
        config = self.prefix / "config.json"
        for value in ("{broken", "[]"):
            with self.subTest(config=value):
                config.write_text(value)
                with patch.object(install.subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "configuration"):
                        self.installer().install()
                run.assert_not_called()
                self.assertEqual(config.read_text(), value)

    def test_model_download_publishes_only_verified_bytes_atomically(self):
        installer = self.installer("--skip-deps")
        installer.model.parent.mkdir(parents=True)
        payload = b"verified model contents"

        def download(command, **kwargs):
            self.assertIn("--fail", command)
            self.assertIn("--location", command)
            self.assertIn("--retry", command)
            # Bound connection attempts and stalled transfers, while allowing a
            # large model to finish over a slow but progressing connection.
            self.assertEqual(command[command.index("--connect-timeout") + 1], "30")
            self.assertEqual(command[command.index("--speed-time") + 1], "60")
            self.assertEqual(command[command.index("--speed-limit") + 1], "1")
            self.assertNotIn("--max-time", command)
            self.assertFalse(installer.model.exists())
            destination = Path(command[command.index("--output") + 1])
            self.assertNotEqual(destination, installer.model)
            self.assertEqual(destination.parent, installer.model.parent)
            destination.write_bytes(payload)

        with patch.dict(install.MODEL_SHA1, base=hashlib.sha1(payload).hexdigest()), patch.object(install.subprocess, "run", side_effect=download) as run:
            installer.download_model()
            installer.download_model()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(installer.model.read_bytes(), payload)
        self.assertEqual(list(installer.model.parent.iterdir()), [installer.model])

    def test_failed_or_corrupt_download_never_publishes_or_leaves_partial_file(self):
        installer = self.installer("--skip-deps")
        installer.model.parent.mkdir(parents=True)
        for returncode, payload in ((22, b""), (18, b"partial transfer"), (28, b"stalled transfer"), (0, b"HTTP error page")):
            with self.subTest(returncode=returncode):
                def download(command, **kwargs):
                    Path(command[command.index("--output") + 1]).write_bytes(payload)
                    if returncode:
                        raise subprocess.CalledProcessError(returncode, command)

                with patch.object(install.subprocess, "run", side_effect=download):
                    with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                        installer.download_model()
                self.assertFalse(installer.model.exists())
                self.assertEqual(list(installer.model.parent.iterdir()), [])

    def test_corrupt_fresh_download_reports_retry_without_manual_removal(self):
        installer = self.installer("--skip-deps")
        installer.model.parent.mkdir(parents=True)

        def download(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"corrupt model")

        with patch.object(install.subprocess, "run", side_effect=download):
            with self.assertRaises(RuntimeError) as failure:
                installer.download_model()
        self.assertIn("checksum mismatch", str(failure.exception))
        self.assertIn("Retry installation", str(failure.exception))
        self.assertNotIn("remove", str(failure.exception))
        self.assertNotIn("left unchanged", str(failure.exception))
        self.assertEqual(list(installer.model.parent.iterdir()), [])

    def test_model_publish_failure_cleans_verified_temporary_file(self):
        installer = self.installer("--skip-deps")
        installer.model.parent.mkdir(parents=True)
        payload = b"verified model contents"

        def download(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(payload)

        with patch.dict(install.MODEL_SHA1, base=hashlib.sha1(payload).hexdigest()), patch.object(install.subprocess, "run", side_effect=download), patch.object(install.os, "replace", side_effect=OSError("read-only target")):
            with self.assertRaisesRegex(OSError, "read-only target"):
                installer.download_model()
        self.assertFalse(installer.model.exists())
        self.assertEqual(list(installer.model.parent.iterdir()), [])

    def test_launcher_restores_discovered_tools_for_desktop_shortcut(self):
        self.prefix.mkdir()
        tools_dir = self.root / "tools with ' quote $(touch NEVER)"
        tools_dir.mkdir()
        recorder = tools_dir / "rec"
        recorder.write_text("#!/bin/sh\nprintf 'recorder available'\n")
        recorder.chmod(0o755)
        (self.source / "scripts" / "whisper_hotkey.py").write_text("import subprocess\nsubprocess.run(['rec'], check=True)\n")
        installer = self.installer("--skip-deps", system="Darwin")
        with patch.object(install.shutil, "which", side_effect=lambda tool: str(recorder) if tool == "rec" else None):
            installer.write_runtime()
        result = subprocess.run([str(self.prefix / "whisper-toggle.sh")], env={"PATH": "/usr/bin:/bin"}, cwd="/", text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout, "recorder available")
        self.assertFalse((self.root / "NEVER").exists())

    def test_config_and_real_launcher_preserve_quoted_paths_and_arguments(self):
        self.prefix.mkdir()
        installer = self.installer("--skip-deps")
        installer.write_runtime()
        config = json.loads((self.prefix / "config.json").read_text())
        self.assertEqual(config["whisper_bin"], str(installer.binary))
        self.assertEqual(config["model_path"], str(installer.model))
        self.assertEqual(config["backend"], "auto")
        self.assertEqual(config["threads"], 4)
        self.assertEqual((self.prefix / "config.json").stat().st_mode & 0o777, 0o600)
        launcher = self.prefix / "whisper-toggle.sh"
        clean_env = dict(os.environ)
        clean_env.pop("WHISPER_HOTKEY_CONFIG", None)
        result = subprocess.run([str(launcher), "transcribe", "audio with ' quote.wav"], env=clean_env, cwd="/", capture_output=True, text=True, check=True)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["config"], str(self.prefix / "config.json"))
        self.assertEqual(actual["args"], ["transcribe", "audio with ' quote.wav"])
        custom = str(self.root / "custom config.json")
        result = subprocess.run([str(launcher)], env={**clean_env, "WHISPER_HOTKEY_CONFIG": custom}, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["config"], custom)
        self.assertFalse((self.root / "NEVER").exists())

    def test_reinstall_preserves_user_settings_and_rejects_bad_config(self):
        self.prefix.mkdir()
        config_path = self.prefix / "config.json"
        config_path.write_text(json.dumps({"language": "fr", "threads": 2, "backend": "sox", "whisper_bin": "obsolete"}))
        installer = self.installer("--skip-deps", "--model", "tiny.en")
        installer.write_runtime()
        config = json.loads(config_path.read_text())
        self.assertEqual(config["language"], "fr")
        self.assertEqual(config["threads"], 2)
        self.assertEqual(config["backend"], "sox")
        self.assertEqual(config["model_path"], str(installer.model))
        config_path.write_text("[]")
        with self.assertRaisesRegex(RuntimeError, "JSON object"):
            installer.write_runtime()
        self.assertEqual(config_path.read_text(), "[]")

    def test_failed_build_never_writes_launcher_or_downloads_model(self):
        installer = self.installer("--skip-deps")
        (installer.checkout / ".git").mkdir(parents=True)
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, stdout=install.WHISPER_COMMIT + "\n" if "rev-parse" in command else "")
            if command[0] == "cmake":
                raise subprocess.CalledProcessError(2, command)
            self.fail(f"Unexpected command after build failure: {command}")

        with patch.object(install.shutil, "which", return_value="/fake/tool"), patch.object(install.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install()
        self.assertFalse((self.prefix / "whisper-toggle.sh").exists())
        self.assertFalse(installer.model.exists())
        self.assertEqual(commands[-1][0], "cmake")

    def test_successful_install_clones_verifies_builds_downloads_then_publishes(self):
        installer = self.installer("--skip-deps", "--model", "tiny.en")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            self.assertFalse((self.prefix / "whisper-toggle.sh").exists())
            if command[:2] == ["git", "clone"]:
                self.assertEqual(command[-1], str(installer.checkout))
                (installer.checkout / ".git").mkdir(parents=True)
                installer.model.parent.mkdir()
            elif command[0] == "git":
                return subprocess.CompletedProcess(command, 0, stdout=install.WHISPER_COMMIT + "\n" if "rev-parse" in command else "")
            elif command[0] == "curl":
                self.assertTrue(command[-1].endswith("ggml-tiny.en.bin"))
                Path(command[command.index("--output") + 1]).write_bytes(b"fake-model")
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(install.MODEL_SHA1, {"tiny.en": hashlib.sha1(b"fake-model").hexdigest()}), patch.object(install.shutil, "which", return_value="/fake/tool"), patch.object(install.subprocess, "run", side_effect=fake_run):
            installer.install()
        self.assertEqual([command[0] for command in commands], ["git", "git", "git", "cmake", "cmake", "curl", str(installer.binary)])
        self.assertEqual(commands[-1], [str(installer.binary), "--help"])
        self.assertTrue((self.prefix / "whisper-toggle.sh").is_file())
        self.assertTrue((self.prefix / "whisper_hotkey.py").is_file())
        self.assertEqual(json.loads((self.prefix / "config.json").read_text())["model_path"], str(installer.model))

    def test_main_reports_command_failure_without_success(self):
        error_output = io.StringIO()
        with patch.object(install.Installer, "install", side_effect=subprocess.CalledProcessError(7, ["cmake", "--build"])), contextlib.redirect_stderr(error_output):
            self.assertEqual(install.main(["--skip-deps"]), 1)
        self.assertIn("Installation failed", error_output.getvalue())

    def test_shell_wrapper_is_independent_of_working_directory(self):
        wrapper = self.source / "install.sh"
        shutil.copyfile(ROOT / "install.sh", wrapper)
        shutil.copyfile(ROOT / "scripts" / "install.py", self.source / "scripts" / "install.py")
        result = subprocess.run(["sh", str(wrapper), "--dry-run", "--skip-deps", "--prefix", str(self.prefix)], cwd="/", capture_output=True, text=True, check=True)
        self.assertIn("Planned shortcut command", result.stdout)
        self.assertFalse(self.prefix.exists())


if __name__ == "__main__":
    unittest.main()
