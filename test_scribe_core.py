import unittest
from unittest.mock import Mock, call, patch

import scribe_core as core


class RecorderTests(unittest.TestCase):
    def setUp(self):
        audio = Mock()
        audio.PortAudioError = core.sd.PortAudioError
        audio._initialized = 1
        self.audio = audio
        audio_patch = patch.object(core, "sd", audio)
        log_patch = patch.object(core, "log")
        for patcher in (audio_patch, log_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_open_error_refreshes_devices_and_retries(self):
        mic = {"name": "USB Mic", "max_input_channels": 1}
        speakers = {"name": "Speakers", "max_input_channels": 0}
        self.audio.query_devices.side_effect = [[mic, speakers], [speakers, mic]]
        stream = Mock()
        self.audio.InputStream.side_effect = [
            self.audio.PortAudioError("Internal PortAudio error", -9986),
            stream,
        ]

        recorder = core.Recorder("USB Mic")
        recorder.start()

        self.assertEqual(self.audio.mock_calls, [
            call.query_devices(),
            call.InputStream(samplerate=16000, channels=1, dtype="int16",
                             device=0, callback=recorder._callback),
            call._terminate(),
            call._initialize(),
            call.query_devices(),
            call.InputStream(samplerate=16000, channels=1, dtype="int16",
                             device=1, callback=recorder._callback),
        ])
        stream.start.assert_called_once_with()
        self.assertIs(recorder.stream, stream)

    def test_failed_stream_start_closes_stream_and_retries_only_once(self):
        failed_stream = self.audio.InputStream.return_value
        failed_stream.start.side_effect = self.audio.PortAudioError("Device unavailable", -9985)
        recorder = core.Recorder(None)

        with self.assertRaisesRegex(self.audio.PortAudioError, "Device unavailable"):
            recorder.start()

        self.assertEqual(self.audio.InputStream.call_count, 2)
        self.assertEqual(failed_stream.close.call_count, 2)
        self.audio._terminate.assert_called_once_with()
        self.audio._initialize.assert_called_once_with()
        self.assertIsNone(recorder.stream)

        healthy_stream = Mock()
        self.audio.InputStream.return_value = healthy_stream
        recorder.start()
        self.audio._initialize.assert_called_once_with()
        self.assertIs(recorder.stream, healthy_stream)
