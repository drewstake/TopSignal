"""Executable release regressions: no broker, database or host connection."""
import hashlib
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import release
from release_lib import archive_members, extract_bundle, safe_name, sha256, verify_bundle, verify_files

spec = importlib.util.spec_from_file_location("release_host", Path(__file__).with_name("release-host.py"))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
spec = importlib.util.spec_from_file_location("benchmark", Path(__file__).with_name("benchmark-backend-tests.py"))
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="ts-release-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()

    def bundle_fixture(self, source=None):
        source = source or {"backend/app/main.py": b"tested source", "scripts/release.py": b"release command"}
        dist = {"frontend/dist/index.html": b"<html>tested</html>", "frontend/dist/assets/a.js": b"code"}
        manifest = {"version": 1, "validated": True, "git_commit": "a" * 40, "archives": {}}
        for name, key, files in (("source.zip", "source_sha256", source), ("frontend.zip", "dist_sha256", dist)):
            with zipfile.ZipFile(self.bundle / name, "w") as archive:
                for path, data in files.items():
                    archive.writestr(path, data)
            manifest[key] = {p: hashlib.sha256(data).hexdigest() for p, data in files.items()}
            manifest["archives"][name] = sha256(self.bundle / name)
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_extracts_verified_bundle_without_changing_previous_release(self):
        self.bundle_fixture()
        previous = self.root / "previous"
        previous.mkdir()
        sentinel = previous / "keep"
        sentinel.write_bytes(b"old source")
        destination = self.root / "new"
        extract_bundle(self.bundle, destination, sha256(self.bundle / "manifest.json"))
        self.assertEqual((destination / "backend/app/main.py").read_bytes(), b"tested source")
        self.assertEqual(sentinel.read_bytes(), b"old source")
        with self.assertRaisesRegex(ValueError, "already exists"):
            extract_bundle(self.bundle, destination, sha256(self.bundle / "manifest.json"))

    def test_archive_tampering_writes_nothing(self):
        self.bundle_fixture()
        with (self.bundle / "frontend.zip").open("ab") as stream:
            stream.write(b"tampered")
        destination = self.root / "new"
        with self.assertRaisesRegex(ValueError, "checksum"):
            extract_bundle(self.bundle, destination, sha256(self.bundle / "manifest.json"))
        self.assertFalse(destination.exists())

    def test_unexpected_files_missing_files_case_aliases_and_traversal_rejected(self):
        for files in ({"../outside": b"x"}, {"A.py": b"x", "a.py": b"x"}, {"backend/.env": b"private"}, {"backend/storage/data": b"history"}):
            with self.subTest(files=list(files)):
                self.bundle_fixture(files)
                with self.assertRaises(ValueError):
                    verify_bundle(self.bundle)
        self.bundle_fixture()
        with self.assertRaisesRegex(ValueError, "inventory"):
            archive_members(self.bundle / "source.zip", {"missing.py": "0" * 64,
                "backend/app/main.py": hashlib.sha256(b"tested source").hexdigest(),
                "scripts/release.py": hashlib.sha256(b"release command").hexdigest()})

    def test_windows_path_escapes_and_devices_rejected(self):
        for path in ("C:/escape", "/escape", "a\\b", "a/../b", "a//b", "./a", "a./b", "NUL.txt", "a/COM1"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_name(path)

    def test_file_drift_detected(self):
        file = self.root / "model.py"
        file.write_text("approved model")
        expected = {file.name: sha256(file)}
        verify_files(self.root, expected)
        file.write_text("different model")
        with self.assertRaisesRegex(ValueError, "drift"):
            verify_files(self.root, expected)

    def test_broker_gate_rejects_exposure_uncertainty_and_changed_settings(self):
        good = {"config_sha256": "a", "runs": {"1": {"id": 2, "status": "running"}},
                "unresolved_attempts": 0, "accounts": [{"simulated": True, "can_trade": True, "positions": 0, "orders": 0}]}
        host.check_gate_snapshot(good, good, True)
        for key, value in (("positions", 1), ("orders", 1), ("simulated", None), ("can_trade", False)):
            bad = json.loads(json.dumps(good))
            bad["accounts"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                host.check_gate_snapshot(bad, good, True)
        for key, value in (("config_sha256", "b"), ("runs", {}), ("unresolved_attempts", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                host.check_gate_snapshot(dict(good, **{key: value}), good, True)
        exposed = json.loads(json.dumps(good))
        exposed["accounts"][0]["positions"] = 1
        host.check_gate_snapshot(exposed, good, False)  # Legitimate post-start trading is preserved.

    def test_recording_gate_rejects_loss_restart_missing_state_and_durability_regression(self):
        first = {"1": {"state": "recording", "capture_id": "same", "durable_events": 10, "dropped_events": 0}}
        host.check_recording(first, {"1": dict(first["1"], durable_events=20)})
        for key, value in (("capture_id", "different"), ("state", "error"), ("durable_events", 9), ("dropped_events", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                host.check_recording(first, {"1": dict(first["1"], **{key: value})})
        with self.assertRaises(ValueError):
            host.check_recording({}, {})

    def test_restart_disarm_is_allowed_only_after_start_and_without_settings_drift(self):
        before = {"config_sha256": "before", "configs": {"1": {"id": "1", "enabled": "True",
                  "updated_at": "before", "order_size": "1"}},
                  "runs": {"1": {"id": 2, "status": "running", "dry_run": False, "stop_reason": None}},
                  "unresolved_attempts": 0,
                  "accounts": [{"simulated": True, "can_trade": True, "positions": 0, "orders": 0}]}
        after = json.loads(json.dumps(before))
        after["config_sha256"] = "after"
        after["configs"]["1"].update(enabled="False", updated_at="after")
        after["runs"]["1"].update(status="stopped", stop_reason="worker_restart_requires_rearm")
        with self.assertRaises(ValueError):
            host.check_gate_snapshot(after, before, True)
        host.check_gate_snapshot(after, before, True, allow_restart_disarm=True)
        for area, key, value in (("configs", "order_size", "2"), ("configs", "enabled", "True"),
                                 ("runs", "id", 3), ("runs", "status", "error"),
                                 ("runs", "stop_reason", "manual_stop"), ("runs", "dry_run", True)):
            bad = json.loads(json.dumps(after))
            bad[area]["1"][key] = value
            with self.subTest(area=area, key=key), self.assertRaises(ValueError):
                host.check_gate_snapshot(bad, before, True, allow_restart_disarm=True)
        after["accounts"][0]["positions"] = 1
        with self.assertRaises(ValueError):
            host.check_gate_snapshot(after, before, True, allow_restart_disarm=True)

    def test_ci_requires_all_jobs_and_exact_commit_and_latest_success(self):
        jobs = [{"name": name, "conclusion": "success"} for name in
                ("Database migrations", "Backend tests", "Frontend tests", "Frontend quality", "Windows compatibility", "verify")]
        listing = [{"databaseId": 5, "headSha": "a" * 40, "conclusion": "success"}]
        detail = {"headSha": "a" * 40, "conclusion": "success", "jobs": jobs, "url": "https://github.com/example/run"}
        with patch.object(release, "capture", side_effect=[json.dumps(listing), json.dumps(detail)]):
            self.assertEqual(release.check_ci("a" * 40, "owner/repo")["run_id"], 5)
        with patch.object(release, "capture", return_value=json.dumps([dict(listing[0], conclusion="failure"), *listing])):
            with self.assertRaises(RuntimeError):
                release.check_ci("a" * 40, "owner/repo")
        for changed in (dict(detail, headSha="b" * 40), dict(detail, jobs=jobs[:-1])):
            with patch.object(release, "capture", side_effect=[json.dumps(listing), json.dumps(changed)]):
                with self.assertRaises(RuntimeError):
                    release.check_ci("a" * 40, "owner/repo")

    def test_benchmark_rejects_missing_skipped_or_duplicate_tests(self):
        from collections import Counter
        baseline = Counter({("suite", "a", "passed"): 1, ("suite", "b", "skipped"): 1})
        benchmark.compare(baseline, baseline.copy())
        for candidate in (Counter({("suite", "a", "passed"): 1}),
                          Counter({("suite", "a", "skipped"): 1, ("suite", "b", "skipped"): 1}),
                          Counter({("suite", "a", "passed"): 2, ("suite", "b", "skipped"): 1})):
            with self.assertRaises(ValueError):
                benchmark.compare(baseline, candidate)

    def test_package_real_git_snapshot_reuses_only_exact_validation_and_build(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "backend").mkdir()
        (repo / "backend/app.py").write_text("tested source")
        (repo / "frontend/dist/assets").mkdir(parents=True)
        (repo / "frontend/dist/index.html").write_text("built")
        (repo / "frontend/dist/assets/a.js").write_text("asset")
        (repo / ".gitignore").write_text("frontend/dist/\nfrontend/.env*\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        original_capture = release.capture
        def local_capture(command, cwd=None):
            return original_capture(command, repo if cwd is None else cwd)
        with patch.object(release, "ROOT", repo), patch.object(release, "capture", side_effect=local_capture):
            receipt = self.root / "validation.json"
            validation = {"validated": True, "input_sha256": release.input_fingerprint(),
                          "git_commit": local_capture(["git", "rev-parse", "HEAD"]),
                          "steps": [{"name": n, "exit_code": 0} for n in
                                    ("backend", "frontend", "lint", "scripts", "root-audit", "frontend-audit", "production-build")],
                          "dist_sha256": {p.relative_to(repo).as_posix(): sha256(p) for p in (repo / "frontend/dist").rglob("*") if p.is_file()}}
            receipt.write_text(json.dumps(validation))
            output = self.root / "packaged"
            release.package(output, receipt, 4)
            self.assertEqual(verify_bundle(output)["git_commit"], validation["git_commit"])
            (repo / "frontend/dist/assets/a.js").write_text("changed artifact")
            with self.assertRaisesRegex(RuntimeError, "assets changed"):
                release.package(self.root / "changed-build", receipt, 4)
            (repo / "frontend/dist/assets/a.js").write_text("asset")
            (repo / "frontend/.env.production.local").write_text("VITE_API_BASE_URL=http://invalid")
            with self.assertRaisesRegex(RuntimeError, "exact source/build"):
                release.package(self.root / "changed-env", receipt, 4)
            (repo / "backend/app.py").write_text("uncommitted source")
            with self.assertRaisesRegex(RuntimeError, "dirty"):
                release.package(self.root / "dirty", receipt, 4)

    def test_host_prepares_private_owned_environment_preserves_assets_and_rechecks_every_file(self):
        previous = self.root / "previous"
        candidate = self.root / "candidate"
        for folder in ("backend/.venv/Scripts", "frontend/dist/assets"):
            (previous / folder).mkdir(parents=True)
        (previous / "backend/.env").write_text("AUTH_REQUIRED=true\n")
        (previous / "frontend/.env.production.local").write_text("VITE_DEMO_MODE=false\n")
        (previous / "backend/requirements.txt").write_text("fixture==1\n")
        (previous / "backend/.venv/Scripts/python.exe").write_bytes(b"fixture interpreter")
        (previous / "frontend/dist/index.html").write_text("previous UI")
        (previous / "frontend/dist/assets/previous.js").write_text("previous asset")
        source = {"backend/requirements.txt": b"fixture==1\n", "backend/app.py": b"candidate",
                  "scripts/preserve-frontend-assets.py": Path(__file__).with_name("preserve-frontend-assets.py").read_bytes()}
        self.bundle_fixture(source)
        config = {"manifest_sha256": sha256(self.bundle / "manifest.json"), "git_commit": "a" * 40, "companion_files": {}}
        (self.bundle / "host.json").write_text(json.dumps(config))
        baseline = host.inventory(previous)
        commands = []
        def command(args, **kwargs):
            commands.append(args)
            if args[0] == "icacls.exe":
                self.assertFalse((candidate / "backend/.env").exists())
            return subprocess.CompletedProcess(args, 0)
        with patch.object(host.subprocess, "run", side_effect=command), patch.object(host.subprocess, "check_output", return_value=str(candidate / "backend/.venv") + "\n"):
            host.prepare(self.bundle, config, candidate, previous)
        self.assertEqual(host.inventory(previous), baseline)
        self.assertEqual((candidate / "backend/.env").read_bytes(), (previous / "backend/.env").read_bytes())
        self.assertEqual((candidate / "frontend/.env.production.local").read_bytes(), (previous / "frontend/.env.production.local").read_bytes())
        self.assertTrue((candidate / "frontend/dist/assets/previous.js").is_file())
        self.assertFalse((candidate / "backend/.venv").is_symlink())
        self.assertEqual(commands[0][0], "icacls.exe")
        self.assertEqual(commands[-1][-3:], ["-m", "pip", "check"])
        host.verify(self.bundle, candidate, previous)
        # Idempotent only after preparation completed and all hashes still match.
        host.prepare(self.bundle, config, candidate, previous)
        (candidate / "backend/.venv/Scripts/python.exe").write_bytes(b"tampered interpreter")
        with self.assertRaisesRegex(ValueError, "dependency files"):
            host.verify(self.bundle, candidate, previous)
        self.assertEqual(host.inventory(previous), baseline)

    def test_remote_stages_tested_bootstrap_and_never_activates_without_ci(self):
        source = {f"scripts/{name}": b"validated bootstrap " + name.encode() for name in
                  ("release-host.ps1", "release-host.py", "release_lib.py")}
        self.bundle_fixture(source)
        config = {"host": "user@host", "key": "C:/keys/deploy", "staging_root": "C:/staging",
                  "releases_root": "C:/releases", "previous_release": "C:/releases/previous",
                  "runtime_root": "C:/runtime", "release_id": "candidate", "repository": "owner/repo",
                  "credential_script": "C:/private/credential.ps1", "bot_config_ids": [1], "companion_files": {}}
        config_path = self.root / "host.json"
        config_path.write_text(json.dumps(config))
        commands = []
        def run(args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(release.subprocess, "run", side_effect=run), patch.object(release, "check_ci", side_effect=RuntimeError("CI pending")):
            with self.assertRaisesRegex(RuntimeError, "CI pending"):
                release.remote(self.bundle, config_path, True)
        bodies = [base64.b64decode(c[-1]).decode("utf-16le") for c in commands if c[0] == "ssh"]
        self.assertTrue(any("-Phase Prepare" in body for body in bodies))
        self.assertFalse(any("-Phase Activate" in body for body in bodies))
        self.assertEqual((self.bundle / "bootstrap/release-host.ps1").read_bytes(), source["scripts/release-host.ps1"])
        self.assertTrue(all("StrictHostKeyChecking=yes" in c for c in commands))
        commands.clear()
        ci = {"commit": "a" * 40, "run_id": 5, "url": "https://github.com/owner/repo/actions/runs/5", "verified_at": 1}
        with patch.object(release.subprocess, "run", side_effect=run), patch.object(release, "check_ci", return_value=ci):
            release.remote(self.bundle, config_path, True)
        last = base64.b64decode(commands[-1][-1]).decode("utf-16le")
        self.assertIn("-Phase Activate", last)
        self.assertIn(sha256(self.bundle / "bootstrap/ci.json"), last)
        commands.clear()
        with patch.object(release.subprocess, "run", side_effect=run), patch.object(release, "check_ci", return_value=ci):
            release.remote(self.bundle, config_path, True, prepared=True)
        self.assertEqual(len(commands), 2)  # CI receipt transfer, then guarded activation only.
        last = base64.b64decode(commands[-1][-1]).decode("utf-16le")
        self.assertIn("-BundleHash", last)
        self.assertNotIn("-Phase Prepare", last)


if __name__ == "__main__":
    unittest.main()
