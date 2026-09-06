"""
Command Tests
===============

Unit tests for the command module: subprocess execution with environment variables.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from usage_monitor_for_claude import command
from usage_monitor_for_claude.platforms import no_window_kwargs
from usage_monitor_for_claude.command import run_event_command

# A frozen build's executable, written the way the Windows build does.
_FROZEN_EXECUTABLE = r'C:\Program Files\MyApp\app.exe'


class TestRunEventCommand(unittest.TestCase):
    """Tests for run_event_command() subprocess launching."""

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_command_executed_with_shell(self, mock_popen: MagicMock):
        """Command is passed to Popen with shell=True."""
        run_event_command(['echo hello'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], 'echo hello')
        self.assertTrue(kwargs['shell'])

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_env_vars_merged_into_environment(self, mock_popen: MagicMock):
        """Event-specific variables are merged into the process environment."""
        env_vars = {
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': 'five_hour',
            'USAGE_MONITOR_UTILIZATION': '84.5',
        }
        run_event_command(['notify.bat'], env_vars)

        passed_env = mock_popen.call_args[1]['env']
        for key, value in env_vars.items():
            self.assertEqual(passed_env[key], value)

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_version_env_var_always_present(self, mock_popen: MagicMock):
        """USAGE_MONITOR_VERSION is always set from __version__."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        passed_env = mock_popen.call_args[1]['env']
        from usage_monitor_for_claude import __version__
        self.assertEqual(passed_env['USAGE_MONITOR_VERSION'], __version__)

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_existing_env_preserved(self, mock_popen: MagicMock):
        """Existing environment variables are preserved alongside new ones."""
        with patch.dict('os.environ', {'PATH': '/usr/bin', 'HOME': '/home/user'}):
            run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        passed_env = mock_popen.call_args[1]['env']
        self.assertEqual(passed_env['PATH'], '/usr/bin')
        self.assertEqual(passed_env['HOME'], '/home/user')
        self.assertEqual(passed_env['USAGE_MONITOR_EVENT'], 'reset')

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_stdout_stderr_devnull(self, mock_popen: MagicMock):
        """stdout and stderr are redirected to DEVNULL."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_create_no_window_flag(self, mock_popen: MagicMock):
        """CREATE_NO_WINDOW flag is set."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        self.assertEqual({k: kwargs.get(k) for k in no_window_kwargs()}, no_window_kwargs())

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_empty_list_skipped(self, mock_popen: MagicMock):
        """Empty command list does not invoke Popen."""
        run_event_command([], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_popen.assert_not_called()

    @patch('usage_monitor_for_claude.command.traceback.print_exc')
    @patch('usage_monitor_for_claude.command.subprocess.Popen', side_effect=OSError('not found'))
    def test_popen_exception_caught(self, mock_popen: MagicMock, mock_print_exc: MagicMock):
        """OSError from Popen is caught and printed to stderr."""
        run_event_command(['nonexistent_command'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_print_exc.assert_called_once()

    @patch('usage_monitor_for_claude.command.traceback.print_exc')
    @patch('usage_monitor_for_claude.command.subprocess.Popen', side_effect=ValueError('bad'))
    def test_unexpected_exception_caught(self, mock_popen: MagicMock, mock_print_exc: MagicMock):
        """Unexpected exceptions from Popen are caught and printed to stderr."""
        run_event_command(['bad_command'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_print_exc.assert_called_once()

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_popen_not_waited(self, mock_popen: MagicMock):
        """Popen result is not waited on (fire-and-forget)."""
        run_event_command(['long_running'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_process = mock_popen.return_value
        mock_process.wait.assert_not_called()
        mock_process.communicate.assert_not_called()

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_cwd_set_to_project_root(self, mock_popen: MagicMock):
        """Working directory is set to the project root (non-frozen)."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        expected = Path(__file__).resolve().parent.parent
        self.assertEqual(kwargs['cwd'], expected)

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    @patch('usage_monitor_for_claude.command.sys')
    def test_cwd_set_to_executable_dir_when_frozen(self, mock_sys: MagicMock, mock_popen: MagicMock):
        """Working directory is set to the executable's folder when frozen."""
        mock_sys.frozen = True
        mock_sys.executable = _FROZEN_EXECUTABLE

        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        self.assertEqual(kwargs['cwd'], Path(_FROZEN_EXECUTABLE).parent)

    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_multiple_commands_all_executed(self, mock_popen: MagicMock):
        """All commands in the list are launched."""
        run_event_command(['cmd1', 'cmd2', 'cmd3'], {'USAGE_MONITOR_EVENT': 'reset'})

        self.assertEqual(mock_popen.call_count, 3)
        executed = [call[0][0] for call in mock_popen.call_args_list]
        self.assertEqual(executed, ['cmd1', 'cmd2', 'cmd3'])

    @patch('usage_monitor_for_claude.command.traceback.print_exc')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_one_failure_does_not_block_others(self, mock_popen: MagicMock, mock_print_exc: MagicMock):
        """A failing command does not prevent subsequent commands from running."""
        mock_popen.side_effect = [OSError('fail'), MagicMock()]

        run_event_command(['bad', 'good'], {'USAGE_MONITOR_EVENT': 'reset'})

        self.assertEqual(mock_popen.call_count, 2)
        mock_print_exc.assert_called_once()


class TestRunEventCommandCaptureOutput(unittest.TestCase):
    """Tests for the capture_output path used by the 'Test event commands' menu."""

    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_capture_output_uses_pipes(self, mock_popen: MagicMock, _thread: MagicMock):
        """With capture_output, stdout and stderr are captured via PIPE (not DEVNULL)."""
        run_event_command(['echo hi'], {'USAGE_MONITOR_EVENT': 'reset'}, capture_output=True)

        kwargs = mock_popen.call_args[1]
        self.assertEqual(kwargs['stdout'], subprocess.PIPE)
        self.assertEqual(kwargs['stderr'], subprocess.PIPE)
        self.assertTrue(kwargs['text'])
        self.assertEqual({k: kwargs.get(k) for k in no_window_kwargs()}, no_window_kwargs())

    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_capture_output_reports_on_background_daemon_thread(self, mock_popen: MagicMock, mock_thread: MagicMock):
        """The wait/report runs on a started daemon thread, so the caller is not blocked."""
        run_event_command(['echo hi'], {'USAGE_MONITOR_EVENT': 'reset'}, capture_output=True)

        self.assertTrue(mock_thread.call_args[1]['daemon'])
        mock_thread.return_value.start.assert_called_once()

    @patch('usage_monitor_for_claude.command.show_error_box')
    @patch('builtins.print')
    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_capture_output_prints_streams_and_exit_code(self, mock_popen: MagicMock, mock_thread: MagicMock, mock_print: MagicMock, _box: MagicMock):
        """Once the command exits, its stdout, stderr, and exit code are printed."""
        mock_popen.return_value.communicate.return_value = ('hello out', 'boom err')
        mock_popen.return_value.returncode = 9

        run_event_command(['bad'], {'USAGE_MONITOR_EVENT': 'reset'}, capture_output=True)

        # Run the report body synchronously (it would otherwise run on the daemon thread).
        mock_thread.call_args[1]['target']()

        printed = '\n'.join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn('hello out', printed)
        self.assertIn('boom err', printed)
        self.assertIn('9', printed)

    @patch('usage_monitor_for_claude.command.show_error_box')
    @patch('builtins.print')
    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_capture_output_reports_empty_streams(self, mock_popen: MagicMock, mock_thread: MagicMock, mock_print: MagicMock, _box: MagicMock):
        """Empty output still reports (no crash), marking streams as empty."""
        mock_popen.return_value.communicate.return_value = ('', '')
        mock_popen.return_value.returncode = 0

        run_event_command(['ok'], {'USAGE_MONITOR_EVENT': 'reset'}, capture_output=True)
        mock_thread.call_args[1]['target']()

        printed = '\n'.join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn('(empty)', printed)

    @patch('usage_monitor_for_claude.command.show_error_box')
    @patch('builtins.print')
    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_capture_output_shows_error_box_on_nonzero_exit(self, mock_popen: MagicMock, mock_thread: MagicMock, mock_print: MagicMock, mock_box: MagicMock):
        """A non-zero exit code raises an error message box containing stderr."""
        mock_popen.return_value.communicate.return_value = ('', 'the failure detail')
        mock_popen.return_value.returncode = 2

        run_event_command(['bad'], {'USAGE_MONITOR_EVENT': 'reset'}, capture_output=True)
        mock_thread.call_args[1]['target']()

        mock_box.assert_called_once()
        box_message = mock_box.call_args[0][0]
        self.assertIn('the failure detail', box_message)
        self.assertIn('2', box_message)

    @patch('usage_monitor_for_claude.command.show_error_box')
    @patch('builtins.print')
    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_capture_output_no_error_box_on_success(self, mock_popen: MagicMock, mock_thread: MagicMock, mock_print: MagicMock, mock_box: MagicMock):
        """A zero exit code shows no error box."""
        mock_popen.return_value.communicate.return_value = ('done', '')
        mock_popen.return_value.returncode = 0

        run_event_command(['ok'], {'USAGE_MONITOR_EVENT': 'reset'}, capture_output=True)
        mock_thread.call_args[1]['target']()

        mock_box.assert_not_called()


class TestStartupFailureWindow(unittest.TestCase):
    """Tests for report_late_failures, which limits the error box to startup failures."""

    def _run(self, returncode: int, runtime: float, report_late_failures: bool):
        """Run one capture_output command with a mocked runtime.

        Returns the MessageBoxW mock and everything the command printed.
        """
        # First call is the launch timestamp, every later one the exit time, so a
        # stray call from an unrelated thread cannot exhaust the mocked clock.
        launch_timestamps = iter([0.0])

        def fake_monotonic() -> float:
            return next(launch_timestamps, runtime)

        with (
            patch('usage_monitor_for_claude.command.show_error_box') as mock_box,
            patch('builtins.print') as mock_print,
            patch('usage_monitor_for_claude.command.threading.Thread') as mock_thread,
            patch('usage_monitor_for_claude.command.time.monotonic', fake_monotonic),
            patch('usage_monitor_for_claude.command.subprocess.Popen') as mock_popen,
        ):
            mock_popen.return_value.communicate.return_value = ('', 'the failure detail')
            mock_popen.return_value.returncode = returncode

            run_event_command(
                ['app.exe'], {'USAGE_MONITOR_EVENT': 'double_click'},
                capture_output=True, report_late_failures=report_late_failures,
            )
            mock_thread.call_args[1]['target']()

        printed = '\n'.join(str(call.args[0]) for call in mock_print.call_args_list)

        return mock_box, printed

    def test_late_failure_suppressed_when_late_reporting_is_off(self):
        """An app that ran for a while and then exited non-zero raises no dialog."""
        runtime = command._STARTUP_FAILURE_WINDOW + 60.0
        message_box, _printed = self._run(returncode=1, runtime=runtime, report_late_failures=False)

        message_box.assert_not_called()

    def test_suppressed_failure_is_still_printed(self):
        """A suppressed failure stays visible on the console - only the dialog is dropped."""
        runtime = command._STARTUP_FAILURE_WINDOW + 60.0
        message_box, printed = self._run(returncode=1, runtime=runtime, report_late_failures=False)

        message_box.assert_not_called()
        self.assertIn('the failure detail', printed)
        self.assertIn('exit code: 1', printed)

    def test_startup_failure_still_reported_when_late_reporting_is_off(self):
        """A command failing right after launch (wrong path) still raises the dialog."""
        message_box, _printed = self._run(returncode=1, runtime=0.2, report_late_failures=False)

        message_box.assert_called_once()

    def test_failure_at_the_window_boundary_is_reported(self):
        """The window is inclusive - a failure exactly at the limit still reports."""
        runtime = command._STARTUP_FAILURE_WINDOW
        message_box, _printed = self._run(returncode=1, runtime=runtime, report_late_failures=False)

        message_box.assert_called_once()

    def test_late_failure_reported_by_default(self):
        """The 'Test event commands' menu keeps the default: any non-zero exit reports."""
        runtime = command._STARTUP_FAILURE_WINDOW + 60.0
        message_box, _printed = self._run(returncode=1, runtime=runtime, report_late_failures=True)

        message_box.assert_called_once()

    def test_success_never_reports(self):
        """A zero exit code shows no dialog, however long the command ran."""
        runtime = command._STARTUP_FAILURE_WINDOW + 60.0
        message_box, _printed = self._run(returncode=0, runtime=runtime, report_late_failures=False)

        message_box.assert_not_called()

    @patch('usage_monitor_for_claude.command.threading.Thread')
    @patch('usage_monitor_for_claude.command.subprocess.Popen')
    def test_no_effect_without_capture_output(self, mock_popen: MagicMock, mock_thread: MagicMock):
        """Without capture_output the flag changes nothing - the command stays fire-and-forget."""
        run_event_command(['app.exe'], {'USAGE_MONITOR_EVENT': 'reset'}, report_late_failures=False)

        self.assertEqual(mock_popen.call_args[1]['stdout'], subprocess.DEVNULL)
        mock_thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
