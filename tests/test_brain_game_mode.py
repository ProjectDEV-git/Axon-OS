"""Brain in game mode: unload models, never load onto the GPU, route to cloud or CPU."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from game_gate import GAME_MODE_MESSAGE, GameGate
from providers import ProviderError

from services.axon_brain.brain_service import BrainService

OLLAMA = "http://localhost:11434"


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def brain(clock):
    service = BrainService.__new__(BrainService)
    service.config = {
        "speed_model": "llama3.2:1b",
        "general_model": "qwen2.5:7b",
        "deep_model": "qwen2.5:14b",
    }
    service._config_lock = threading.RLock()
    service._streams_lock = threading.Lock()
    service._active_streams = {}
    service._gate = GameGate(clock)
    service._installed_models = MagicMock(return_value=["llama3.2:1b", "qwen2.5:7b"])
    service.SuspendedChanged = MagicMock()
    service.notify_property_changed = MagicMock()
    # Suspend() starts this in a thread; tests that cover it call the real one directly
    service._unload_all_models = MagicMock()
    return service


def sent(opener):
    """The JSON body of the request given to the (mocked) HTTP opener."""
    request = opener.call_args.args[0]
    return json.loads(request.data)


@pytest.fixture
def opener():
    with patch.object(BrainService, "_open_with_retry", return_value=MagicMock()) as m:
        yield m


@pytest.mark.unit
class TestRequestsWhileAwake:
    def test_requests_pass_through_unchanged(self, brain, opener):
        brain._http_post(f"{OLLAMA}/api/generate", {"model": "m", "prompt": "hi"})
        assert sent(opener) == {"model": "m", "prompt": "hi"}


@pytest.mark.unit
class TestRequestsWhileSuspended:
    @pytest.mark.parametrize(
        ("path", "payload"),
        [
            ("/api/generate", {"model": "m", "prompt": "hi"}),
            ("/api/generate", {"model": "m"}),  # the warm-up request: loads a model
            ("/api/chat", {"model": "m", "messages": []}),
            ("/api/embed", {"model": "nomic-embed-text", "input": "x"}),
            ("/api/embeddings", {"model": "nomic-embed-text", "prompt": "x"}),
        ],
    )
    def test_gpu_work_is_refused_with_the_game_mode_message(self, brain, opener, path, payload):
        brain.Suspend("test")
        with pytest.raises(ProviderError, match=GAME_MODE_MESSAGE):
            brain._http_post(f"{OLLAMA}{path}", payload)
        opener.assert_not_called()

    def test_unloading_a_model_is_allowed(self, brain, opener):
        brain.Suspend("test")
        brain._http_post(f"{OLLAMA}/api/generate", {"model": "m", "keep_alive": 0})
        assert sent(opener) == {"model": "m", "keep_alive": 0}

    def test_a_generate_request_with_keep_alive_zero_and_a_prompt_is_refused(self, brain, opener):
        brain.Suspend("test")
        with pytest.raises(ProviderError):
            brain._http_post(
                f"{OLLAMA}/api/generate", {"model": "m", "prompt": "x", "keep_alive": 0}
            )

    def test_model_downloads_still_work(self, brain, opener):
        brain.Suspend("test")
        brain._http_post(f"{OLLAMA}/api/pull", {"name": "llama3.2:1b"})
        opener.assert_called_once()

    def test_resume_allows_requests_again(self, brain, opener):
        brain.Suspend("test")
        brain.Resume()
        brain._http_post(f"{OLLAMA}/api/chat", {"model": "m", "messages": []})
        opener.assert_called_once()

    def test_embeddings_report_an_error_and_never_reach_ollama(self, brain, opener):
        brain.Suspend("test")
        result = json.loads(brain.GetEmbeddings("hello", "nomic-embed-text"))
        assert GAME_MODE_MESSAGE in result["error"]
        opener.assert_not_called()


@pytest.mark.unit
class TestRunAnyway:
    def test_request_moves_to_the_speed_model_on_cpu(self, brain, opener):
        brain.Suspend("test")
        assert brain.AllowCpuOnly(120)
        brain._http_post(
            f"{OLLAMA}/api/chat", {"model": "qwen2.5:7b", "messages": [], "options": {"seed": 1}}
        )
        body = sent(opener)
        assert body["model"] == "llama3.2:1b"
        assert body["options"] == {"seed": 1, "num_gpu": 0}
        assert body["keep_alive"] == 0

    def test_embeddings_run_on_cpu_with_their_own_model(self, brain, opener):
        brain.Suspend("test")
        brain.AllowCpuOnly(120)
        brain._http_post(f"{OLLAMA}/api/embed", {"model": "nomic-embed-text", "input": "x"})
        body = sent(opener)
        assert body["model"] == "nomic-embed-text"
        assert body["options"]["num_gpu"] == 0

    def test_window_expires(self, brain, opener, clock):
        brain.Suspend("test")
        brain.AllowCpuOnly(120)
        clock.now += 121
        with pytest.raises(ProviderError):
            brain._http_post(f"{OLLAMA}/api/chat", {"model": "m", "messages": []})

    def test_window_is_capped(self, brain, clock):
        brain.Suspend("test")
        brain.AllowCpuOnly(10**6)
        clock.now += 601
        assert not brain._gate.cpu_allowed

    def test_cannot_be_granted_while_awake(self, brain):
        assert brain.AllowCpuOnly(120) is False

    def test_resume_cancels_the_window(self, brain, opener):
        brain.Suspend("test")
        brain.AllowCpuOnly(120)
        brain.Resume()
        brain.Suspend("again")
        with pytest.raises(ProviderError):
            brain._http_post(f"{OLLAMA}/api/chat", {"model": "m", "messages": []})


@pytest.mark.unit
class TestCloudDuringGames:
    def test_local_request_goes_to_a_cloud_tier_model(self, brain):
        brain.config["general_model"] = "@openai/gpt-4o-mini"
        brain.Suspend("test")
        brain.SetGameModeCloud(True)
        assert brain._game_mode_model("llama3.2:1b") == "@openai/gpt-4o-mini"

    def test_explicit_game_mode_cloud_model_wins(self, brain):
        brain.config["game_mode_cloud_model"] = "@anthropic/claude-haiku"
        brain.config["general_model"] = "@openai/gpt-4o-mini"
        brain.Suspend("test")
        brain.SetGameModeCloud(True)
        assert brain._game_mode_model("llama3.2:1b") == "@anthropic/claude-haiku"

    def test_without_a_cloud_model_the_request_stays_local_and_is_refused(self, brain):
        brain.Suspend("test")
        brain.SetGameModeCloud(True)
        assert brain._game_mode_model("llama3.2:1b") == "llama3.2:1b"

    def test_setting_off_keeps_local_models(self, brain):
        brain.config["general_model"] = "@openai/gpt-4o-mini"
        brain.Suspend("test")
        assert brain._game_mode_model("llama3.2:1b") == "llama3.2:1b"

    def test_awake_brain_never_reroutes(self, brain):
        brain.config["general_model"] = "@openai/gpt-4o-mini"
        brain.SetGameModeCloud(True)
        assert brain._game_mode_model("llama3.2:1b") == "llama3.2:1b"

    def test_cloud_models_are_left_alone(self, brain):
        brain.Suspend("test")
        brain.SetGameModeCloud(True)
        assert brain._game_mode_model("@openai/gpt-4o") == "@openai/gpt-4o"


@pytest.mark.unit
class TestSuspendAndResume:
    def test_suspend_reports_state_and_signals_once(self, brain):
        brain.Suspend("game")
        brain.Suspend("game again")
        assert brain.IsSuspended()
        brain.SuspendedChanged.assert_called_once_with(True)
        assert brain.get_dbus_properties("org.axonos.Brain") == {"Suspended": True}

    def test_resume_signals_once_and_does_not_reload_models(self, brain, opener):
        brain.Suspend("game")
        brain.Resume()
        brain.Resume()
        assert not brain.IsSuspended()
        assert brain.SuspendedChanged.call_args_list[-1].args == (False,)
        assert brain.SuspendedChanged.call_count == 2
        opener.assert_not_called()  # nothing is loaded until the next request

    def test_suspend_cancels_running_streams(self, brain):
        flag = threading.Event()
        brain._active_streams["tx"] = flag
        brain.Suspend("game")
        assert flag.is_set()

    def test_unrelated_interfaces_have_no_properties(self, brain):
        assert brain.get_dbus_properties("org.axonos.Other") == {}


def _response(data):
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = json.dumps(data).encode()
    return resp


@pytest.mark.unit
class TestUnloadingModels:
    def test_every_loaded_model_gets_a_keep_alive_zero_request(self, brain):
        brain._http_get = MagicMock(
            return_value=_response(
                {"models": [{"name": "llama3.2:1b"}, {"model": "nomic-embed-text"}]}
            )
        )
        brain._http_post = MagicMock(return_value=_response({}))
        brain._gate.suspend()
        with patch("services.axon_brain.brain_service.time.sleep"):
            BrainService._unload_all_models(brain)
        unloaded = [c.args[1] for c in brain._http_post.call_args_list]
        assert {"model": "llama3.2:1b", "keep_alive": 0} in unloaded
        assert {"model": "nomic-embed-text", "keep_alive": 0} in unloaded
        brain._http_get.assert_called_with(f"{OLLAMA}/api/ps", timeout=3.0, max_retries=1)

    def test_second_pass_catches_a_model_loaded_in_the_meantime(self, brain):
        brain._http_get = MagicMock(
            side_effect=[_response({"models": []}), _response({"models": [{"name": "late"}]})]
        )
        brain._http_post = MagicMock(return_value=_response({}))
        brain._gate.suspend()
        with patch("services.axon_brain.brain_service.time.sleep"):
            BrainService._unload_all_models(brain)
        assert brain._http_post.call_args.args[1] == {"model": "late", "keep_alive": 0}

    def test_stops_when_game_mode_ended(self, brain):
        brain._http_get = MagicMock(return_value=_response({"models": [{"name": "m"}]}))
        brain._http_post = MagicMock()
        with patch("services.axon_brain.brain_service.time.sleep"):
            BrainService._unload_all_models(brain)  # not suspended
        brain._http_post.assert_not_called()

    def test_ollama_down_is_not_an_error(self, brain):
        brain._http_get = MagicMock(side_effect=OSError("refused"))
        brain._gate.suspend()
        BrainService._unload_all_models(brain)


@pytest.mark.unit
def test_warm_up_is_skipped_during_game_mode(brain):
    brain._gate.suspend()
    brain._http_post = MagicMock()
    brain._warm_up_ollama(wait_seconds=1)
    brain._http_post.assert_not_called()


@pytest.mark.unit
def test_suspend_unloads_models_in_the_background(brain):
    started = threading.Event()
    brain._unload_all_models.side_effect = lambda: started.set()
    brain.Suspend("game")
    assert started.wait(2)
