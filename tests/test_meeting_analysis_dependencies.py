from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "skills" / "meeting-analysis" / "scripts"
INSTALLER = SCRIPTS_ROOT / "install_deps.sh"
PREFLIGHT = SCRIPTS_ROOT / "check_deps.sh"
TRANSCRIPT_DIFF = SCRIPTS_ROOT / "transcript_diff.py"
BASH = shutil.which("bash")


class MeetingAnalysisDependencyTests(unittest.TestCase):
    def _installer_environment(
        self, root: Path, extra_path: Path | None = None
    ) -> dict[str, str]:
        environment = dict(os.environ)
        environment.pop("MEETING_ANALYSIS_AUTO_INSTALL", None)
        environment["HOME"] = str(root / "home")
        environment["LOCALAPPDATA"] = str(root / "local-app-data")
        environment["MEETING_ANALYSIS_VENV"] = str(root / "venv")
        environment["PATH"] = str(extra_path) if extra_path else ""
        return environment

    def test_installer_requires_opt_in_and_prints_winget_commands_without_running_them(
        self,
    ) -> None:
        self.assertIsNotNone(BASH, "test fixture needs bash")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "winget-ran"
            winget = bin_dir / "winget"
            winget.write_text(
                f"#!/usr/bin/env bash\ntouch {marker}\n", encoding="utf-8"
            )
            winget.chmod(0o755)
            result = subprocess.run(
                [str(BASH), str(INSTALLER)],
                env=self._installer_environment(root, bin_dir),
                check=False,
                capture_output=True,
                text=True,
            )
            winget_ran = marker.exists()

        self.assertEqual(result.returncode, 1)
        self.assertIn("automatic installation is disabled", result.stderr)
        self.assertIn("manual: winget install --id Gyan.FFmpeg", result.stderr)
        self.assertIn("manual: winget install --id Xpdf.XpdfReader", result.stderr)
        self.assertFalse(winget_ran, "the opt-in gate must make winget unreachable")

    def test_installer_prints_apt_manual_commands_when_winget_is_unavailable(
        self,
    ) -> None:
        self.assertIsNotNone(BASH, "test fixture needs bash")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            apt_get = bin_dir / "apt-get"
            apt_get.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            apt_get.chmod(0o755)
            result = subprocess.run(
                [str(BASH), str(INSTALLER)],
                env=self._installer_environment(root, bin_dir),
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("manual: sudo apt-get install -y ffmpeg", result.stderr)
        self.assertIn("manual: sudo apt-get install -y poppler-utils", result.stderr)

    def test_transcript_only_preflight_reports_lost_modalities_and_transcript_diff_runs(
        self,
    ) -> None:
        self.assertIsNotNone(BASH, "test fixture needs bash")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = self._installer_environment(root)
            environment["PATH"] = str(root / "empty-bin")
            (root / "empty-bin").mkdir()
            preflight = subprocess.run(
                [
                    str(BASH),
                    "-c",
                    f'source "{PREFLIGHT}" && meeting_analysis_preflight --transcript-only',
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            vtt = root / "[Team Sync] Weekly.vtt"
            vtt.write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nWe reviewed the delivery plan.\n",
                encoding="utf-8",
            )
            asr = root / "asr.jsonl"
            asr.write_text(
                json.dumps(
                    {
                        "text": "We reviewed the delivery plan",
                        "words": [{"s": 1, "w": "delivery", "p": 0.99}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            transcript_diff = subprocess.run(
                [
                    sys.executable,
                    str(TRANSCRIPT_DIFF),
                    str(vtt),
                    str(asr),
                    "--min-freq",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        self.assertIn(
            "transcript-only mode; usable: vendor-transcript reading", preflight.stderr
        )
        self.assertIn("ffmpeg (ASR and frame analysis unavailable)", preflight.stderr)
        self.assertIn("ffprobe (usable-duration bound unavailable)", preflight.stderr)
        self.assertEqual(transcript_diff.returncode, 0, transcript_diff.stderr)
        self.assertIn("vendor cues=1 asr segments=1", transcript_diff.stdout)
        self.assertIn("ASR-only terms", transcript_diff.stdout)


if __name__ == "__main__":
    unittest.main()
