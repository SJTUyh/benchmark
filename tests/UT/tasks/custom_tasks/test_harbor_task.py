import unittest
import os
import tempfile
import json
import shutil
from unittest import mock
from pathlib import Path
import sys

sys.modules['harbor'] = mock.MagicMock()
sys.modules['harbor.cli'] = mock.MagicMock()
sys.modules['harbor.cli.utils'] = mock.MagicMock()
sys.modules['harbor.job'] = mock.MagicMock()
sys.modules['harbor.models'] = mock.MagicMock()
sys.modules['harbor.models.job'] = mock.MagicMock()
sys.modules['harbor.models.job.config'] = mock.MagicMock()
sys.modules['harbor.models.agent'] = mock.MagicMock()
sys.modules['harbor.models.agent.name'] = mock.MagicMock()
sys.modules['harbor.models.environment_type'] = mock.MagicMock()

from ais_bench.benchmark.tasks.custom_tasks.harbor_task import HarborTask, DEFAULT_FAKE_API_KEY
from ais_bench.benchmark.utils.config import ConfigDict
from ais_bench.benchmark.tasks.base import TaskStateManager


class TestHarborTask(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

        self.cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{
                "type": "openai",
                "abbr": "terminus-2",
                "agent_name": "terminus-2",
                "model_names": ["hosted_vllm/qwen3"],
                "agent_kwargs": {
                    "api_base": "http://0.0.0.0:8080/v1",
                    "model_info": {
                        "max_input_tokens": 128000,
                        "max_output_tokens": 4096,
                        "input_cost_per_token": 0.0,
                        "output_cost_per_token": 0.0,
                    },
                },
            }],
            "datasets": [[{
                "abbr": "harbor_terminal-bench-2",
                "args": {
                    "n_attempts": 1,
                    "timeout_multiplier": 1.0,
                    "agent_timeout_multiplier": None,
                    "verifier_timeout_multiplier": None,
                    "agent_setup_timeout_multiplier": None,
                    "environment_build_timeout_multiplier": None,
                    "debug": False,
                    "n_concurrent_trials": 5,
                    "quiet": False,
                    "max_retries": 0,
                    "retry_include_exceptions": None,
                    "retry_exclude_exceptions": [
                        "RewardFileEmptyError",
                        "VerifierOutputParseError",
                    ],
                    "environment_type": "docker",
                    "environment_force_build": False,
                    "environment_delete": False,
                    "path": "/path/to/terminal-bench-2/",
                    "dataset_name_version": None,
                    "task_names": None,
                    "exclude_task_names": None,
                    "n_tasks": None,
                    "disable_verification": False,
                    "verifier_env": None,
                    "yes": True,
                    "env_file": None,
                }
            }]],
            "cli_args": {
                "debug": False
            }
        })

        self.task_state_manager = mock.MagicMock(spec=TaskStateManager)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_init(self):
        """Test HarborTask initialization"""
        task = HarborTask(self.cfg)
        self.assertIsNone(task.captured_metrics)
        self.assertIsNone(task.job_dir)
        self.assertIsNone(task.job_result)
        self.assertIsNone(task.job)
        self.assertEqual(task.name_prefix, "HarborTask")
        self.assertEqual(task.log_subdir, "logs/eval")
        self.assertEqual(task.output_subdir, "results")

    def test_get_command(self):
        """Test get_command method"""
        task = HarborTask(self.cfg)
        cfg_path = "test_config.py"
        template = "test_template"
        command = task.get_command(cfg_path, template)

        self.assertIn(cfg_path, command)
        self.assertIn("harbor_task.py", command)
        self.assertIn(sys.executable, command)

    def test_set_api_key_with_api_key(self):
        """Test _set_api_key method with api_key"""
        original_env = os.environ.get("OPENAI_API_KEY")

        cfg_with_key = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{
                "abbr": "test_model",
                "api_key": "test_key_123"
            }],
            "datasets": [[{"abbr": "test_dataset", "args": {}}]],
            "cli_args": {"debug": False}
        })
        task_with_key = HarborTask(cfg_with_key)
        task_with_key._set_api_key()
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "test_key_123")

        if original_env is not None:
            os.environ["OPENAI_API_KEY"] = original_env
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_set_api_key_without_api_key(self):
        """Test _set_api_key method without api_key"""
        original_env = os.environ.get("OPENAI_API_KEY")

        cfg_no_key = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model"}],
            "datasets": [[{"abbr": "test_dataset", "args": {}}]],
            "cli_args": {"debug": False}
        })
        task = HarborTask(cfg_no_key)
        task._set_api_key()
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), DEFAULT_FAKE_API_KEY)

        if original_env is not None:
            os.environ["OPENAI_API_KEY"] = original_env
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_prepare_out_dir(self):
        """Test _prepare_out_dir method"""
        task = HarborTask(self.cfg)
        task._prepare_out_dir()

        expected_out_dir = os.path.join(self.temp_dir, "results", "terminus-2")
        self.assertTrue(os.path.exists(expected_out_dir))

        expected_dataset_dir = os.path.join(expected_out_dir, "harbor_terminal-bench-2")
        self.assertTrue(os.path.exists(expected_dataset_dir))
        self.assertEqual(task.out_dir, expected_out_dir)
        self.assertEqual(task.out_detail_dir, expected_dataset_dir)

    def test_prepare_out_dir_creates_nested_dirs(self):
        """Test _prepare_out_dir creates nested directories"""
        task = HarborTask(self.cfg)
        task._prepare_out_dir()

        self.assertTrue(os.path.exists(task.out_detail_dir))
        self.assertTrue(os.path.isdir(task.out_detail_dir))

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_config_setup(self, mock_run_async):
        """Test _run_harbor_job method config setup"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName
        from harbor.models.environment_type import EnvironmentType
        from harbor.models.agent.config import AgentConfig

        mock_job_config = mock.MagicMock()
        with mock.patch.object(JobConfig, '__init__', return_value=None):
            with mock.patch.object(JobConfig, 'return_value', mock_job_config):
                task = HarborTask(self.cfg)
                task.out_detail_dir = os.path.join(self.temp_dir, "results", "terminus-2", "harbor_terminal-bench-2")
                os.makedirs(task.out_detail_dir, exist_ok=True)

                with mock.patch.object(task, '_get_task_count', return_value=10), \
                     mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
                    task._run_harbor_job()

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_local_path(self, mock_run_async):
        """Test _run_harbor_job with local path"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/local/dataset",
                    "task_names": ["task1", "task2"],
                    "exclude_task_names": ["exclude1"],
                    "n_tasks": 5,
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        with mock.patch.object(task, '_get_task_count', return_value=5), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None):
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_dataset_name_version(self, mock_run_async):
        """Test _run_harbor_job with dataset_name_version"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "dataset_name_version": "terminal-bench@2.0",
                    "task_names": None,
                    "exclude_task_names": None,
                    "n_tasks": None,
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        with mock.patch.object(task, '_get_task_count', return_value=10), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None):
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_resume_job(self, mock_run_async):
        """Test _resume_job method"""
        from harbor.models.job.config import JobConfig
        from harbor.job import Job

        job_dir = Path(self.temp_dir) / "job_dir"
        job_dir.mkdir()

        config_path = job_dir / "config.json"
        config_data = {
            "job_name": "test_job",
            "jobs_dir": str(self.temp_dir),
        }
        with open(config_path, 'w') as f:
            json.dump(config_data, f)

        mock_job = mock.MagicMock()
        mock_job_cls = mock.MagicMock()
        mock_job_cls.create.return_value = mock_job

        task = HarborTask(self.cfg)
        task.logger = mock.MagicMock()

        with mock.patch.dict('sys.modules', {'harbor.job': mock.MagicMock(Job=mock_job_cls)}):
            with mock.patch.object(JobConfig, 'model_validate_json', return_value=mock.MagicMock()):
                mock_result = mock.MagicMock()
                mock_run_async.return_value = (mock_job, mock_result)

                result = task._resume_job(job_dir)

                self.assertEqual(result[0], mock_job)
                self.assertEqual(result[1], mock_result)

    def test_resume_job_missing_config(self):
        """Test _resume_job raises error when config missing"""
        job_dir = Path(self.temp_dir) / "job_dir"
        job_dir.mkdir()

        task = HarborTask(self.cfg)
        task.logger = mock.MagicMock()

        with self.assertRaises(ValueError) as context:
            task._resume_job(job_dir)

        self.assertIn("Config file not found", str(context.exception))

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_n_attempts_multiplier(self, mock_run_async):
        """Test _run_harbor_job with n_attempts multiplier"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/dataset",
                    "n_attempts": 3,
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        with mock.patch.object(task, '_get_task_count', return_value=5), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None):
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_retry_config(self, mock_run_async):
        """Test _run_harbor_job with retry configuration"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/dataset",
                    "max_retries": 5,
                    "retry_include_exceptions": ["TimeoutError", "ConnectionError"],
                    "retry_exclude_exceptions": ["ValueError"],
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        mock_job_config = mock.MagicMock()
        mock_job_config.retry = mock.MagicMock()

        with mock.patch.object(task, '_get_task_count', return_value=1), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None) as mock_job_init:
                mock_job_init.side_effect = lambda: setattr(mock_job_config, 'retry', mock.MagicMock())
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_environment_settings(self, mock_run_async):
        """Test _run_harbor_job with environment settings"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName
        from harbor.models.environment_type import EnvironmentType

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/dataset",
                    "environment_type": "daytona",
                    "environment_force_build": True,
                    "environment_delete": True,
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        with mock.patch.object(task, '_get_task_count', return_value=1), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None):
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    with mock.patch.object(EnvironmentType, '__init__', return_value=None):
                        task._run_harbor_job()

    def test_dump_eval_results_with_none_result(self):
        """Test _dump_eval_results with None result"""
        task = HarborTask(self.cfg)
        task.dataset_cfgs = [{"abbr": "test_dataset"}]
        task.out_dir = os.path.join(self.temp_dir, "results")
        task.logger = mock.MagicMock()

        task._dump_eval_results(mock.MagicMock(), None)

        task.logger.error.assert_called_once()

    def test_dump_eval_results_normal(self):
        """Test _dump_eval_results normal case"""
        task = HarborTask(self.cfg)
        task.dataset_cfgs = [{"abbr": "test_dataset"}]
        task.out_dir = os.path.join(self.temp_dir, "results")
        task.logger = mock.MagicMock()

        os.makedirs(task.out_dir, exist_ok=True)

        mock_job = mock.MagicMock()
        mock_job.config = mock.MagicMock()

        mock_trial_result_1 = mock.MagicMock()
        mock_trial_result_1.exception_info = None
        mock_trial_result_1.verifier_result = mock.MagicMock()
        mock_trial_result_1.verifier_result.rewards = {"accuracy": 1.0}

        mock_trial_result_2 = mock.MagicMock()
        mock_trial_result_2.exception_info = None
        mock_trial_result_2.verifier_result = mock.MagicMock()
        mock_trial_result_2.verifier_result.rewards = {"accuracy": 0.5}

        mock_trial_result_3 = mock.MagicMock()
        mock_trial_result_3.exception_info = mock.MagicMock()
        mock_trial_result_3.exception_info.exception_type = "AgentTimeoutError"

        mock_job_result = mock.MagicMock()
        mock_job_result.trial_results = [mock_trial_result_1, mock_trial_result_2, mock_trial_result_3]
        mock_job_result.n_total_trials = 3
        mock_job_result.stats = mock.MagicMock()
        mock_job_result.stats.evals = {
            "test_eval": mock.MagicMock(pass_at_k={"pass@1": 0.5, "pass@5": 0.8})
        }

        with mock.patch.object(task, '_get_task_count', return_value=3):
            task._dump_eval_results(mock_job, mock_job_result)

        out_json_path = os.path.join(task.out_dir, "test_dataset.json")
        self.assertTrue(os.path.exists(out_json_path))

        with open(out_json_path, 'r') as f:
            results = json.load(f)

        self.assertEqual(results["total_count"], 3)
        self.assertEqual(results["n_errors"], 1)
        self.assertEqual(results["avg_score"], 0.5)
        self.assertIn("pass@1", results["pass_at_k"])

    def test_dump_eval_results_no_trial_results(self):
        """Test _dump_eval_results with no trial results"""
        task = HarborTask(self.cfg)
        task.dataset_cfgs = [{"abbr": "test_dataset"}]
        task.out_dir = os.path.join(self.temp_dir, "results")
        task.logger = mock.MagicMock()

        os.makedirs(task.out_dir, exist_ok=True)

        mock_job = mock.MagicMock()
        mock_job.config = mock.MagicMock()

        mock_job_result = mock.MagicMock()
        mock_job_result.trial_results = []
        mock_job_result.n_total_trials = 0
        mock_job_result.stats = None

        with mock.patch.object(task, '_get_task_count', return_value=0):
            task._dump_eval_results(mock_job, mock_job_result)

        out_json_path = os.path.join(task.out_dir, "test_dataset.json")
        self.assertTrue(os.path.exists(out_json_path))

        with open(out_json_path, 'r') as f:
            results = json.load(f)

        self.assertEqual(results["avg_score"], 0.0)

    def test_dump_eval_results_reward_distribution(self):
        """Test _dump_eval_results reward distribution"""
        task = HarborTask(self.cfg)
        task.dataset_cfgs = [{"abbr": "test_dataset"}]
        task.out_dir = os.path.join(self.temp_dir, "results")
        task.logger = mock.MagicMock()

        os.makedirs(task.out_dir, exist_ok=True)

        mock_job = mock.MagicMock()
        mock_job.config = mock.MagicMock()

        mock_trial_1 = mock.MagicMock()
        mock_trial_1.exception_info = None
        mock_trial_1.verifier_result = mock.MagicMock()
        mock_trial_1.verifier_result.rewards = {"score": 1.0}

        mock_trial_2 = mock.MagicMock()
        mock_trial_2.exception_info = None
        mock_trial_2.verifier_result = mock.MagicMock()
        mock_trial_2.verifier_result.rewards = {"score": 1.0}

        mock_trial_3 = mock.MagicMock()
        mock_trial_3.exception_info = None
        mock_trial_3.verifier_result = mock.MagicMock()
        mock_trial_3.verifier_result.rewards = {"score": 0.0}

        mock_job_result = mock.MagicMock()
        mock_job_result.trial_results = [mock_trial_1, mock_trial_2, mock_trial_3]
        mock_job_result.n_total_trials = 3
        mock_job_result.stats = None

        with mock.patch.object(task, '_get_task_count', return_value=3):
            task._dump_eval_results(mock_job, mock_job_result)

        out_json_path = os.path.join(task.out_dir, "test_dataset.json")
        with open(out_json_path, 'r') as f:
            results = json.load(f)

        self.assertEqual(len(results["reward_distribution"]), 2)
        score_1_entry = next((r for r in results["reward_distribution"] if r["score"] == 1.0), None)
        self.assertIsNotNone(score_1_entry)
        self.assertEqual(score_1_entry["count"], 2)

    def test_dump_eval_results_exception_distribution(self):
        """Test _dump_eval_results exception distribution"""
        task = HarborTask(self.cfg)
        task.dataset_cfgs = [{"abbr": "test_dataset"}]
        task.out_dir = os.path.join(self.temp_dir, "results")
        task.logger = mock.MagicMock()

        os.makedirs(task.out_dir, exist_ok=True)

        mock_job = mock.MagicMock()
        mock_job.config = mock.MagicMock()

        mock_trial_1 = mock.MagicMock()
        mock_trial_1.exception_info = mock.MagicMock()
        mock_trial_1.exception_info.exception_type = "AgentTimeoutError"

        mock_trial_2 = mock.MagicMock()
        mock_trial_2.exception_info = mock.MagicMock()
        mock_trial_2.exception_info.exception_type = "AgentTimeoutError"

        mock_trial_3 = mock.MagicMock()
        mock_trial_3.exception_info = mock.MagicMock()
        mock_trial_3.exception_info.exception_type = "VerifierError"

        mock_job_result = mock.MagicMock()
        mock_job_result.trial_results = [mock_trial_1, mock_trial_2, mock_trial_3]
        mock_job_result.n_total_trials = 3
        mock_job_result.stats = None

        with mock.patch.object(task, '_get_task_count', return_value=3):
            task._dump_eval_results(mock_job, mock_job_result)

        out_json_path = os.path.join(task.out_dir, "test_dataset.json")
        with open(out_json_path, 'r') as f:
            results = json.load(f)

        self.assertEqual(results["n_errors"], 3)
        self.assertEqual(len(results["exception_distribution"]), 2)

    def test_run_method_integration(self):
        """Test run method integration"""
        task = HarborTask(self.cfg)
        task.logger = mock.MagicMock()
        task.task_state_manager = self.task_state_manager

        with mock.patch.object(task, '_set_api_key') as mock_set_key, \
             mock.patch.object(task, '_prepare_out_dir') as mock_prepare, \
             mock.patch.object(task, '_run_harbor_job') as mock_run_job, \
             mock.patch.object(task, '_dump_eval_results') as mock_dump:

            mock_run_job.return_value = (mock.MagicMock(), mock.MagicMock())

            task.run(self.task_state_manager)

            mock_set_key.assert_called_once()
            mock_prepare.assert_called_once()
            mock_run_job.assert_called_once()
            mock_dump.assert_called_once()

    def test_parse_args(self):
        """Test parse_args function"""
        from ais_bench.benchmark.tasks.custom_tasks.harbor_task import parse_args

        original_argv = sys.argv
        try:
            sys.argv = ["harbor_task.py", "test_config.py"]
            args = parse_args()
            self.assertEqual(args.config, "test_config.py")
        finally:
            sys.argv = original_argv

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_verifier_env(self, mock_run_async):
        """Test _run_harbor_job with verifier environment variables"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/dataset",
                    "verifier_env": ["ENV_VAR1=value1", "ENV_VAR2=value2"],
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        with mock.patch.object(task, '_get_task_count', return_value=1), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None):
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_disabled_verification(self, mock_run_async):
        """Test _run_harbor_job with disabled verification"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{"abbr": "test_model", "agent_name": "test_agent"}],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/dataset",
                    "disable_verification": True,
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        mock_job_config = mock.MagicMock()
        mock_job_config.verifier = mock.MagicMock()

        with mock.patch.object(task, '_get_task_count', return_value=1), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None) as mock_job_init:
                mock_job_init.side_effect = lambda: setattr(mock_job_config, 'verifier', mock.MagicMock())
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

                self.assertTrue(mock_job_config.verifier.disable)

    @mock.patch('ais_bench.benchmark.tasks.custom_tasks.harbor_task.run_async')
    def test_run_harbor_job_with_multiple_agents(self, mock_run_async):
        """Test _run_harbor_job with multiple agents"""
        from harbor.models.job.config import JobConfig
        from harbor.models.agent.name import AgentName

        cfg = ConfigDict({
            "work_dir": self.temp_dir,
            "models": [{
                "abbr": "test_model",
                "agent_name": "test_agent",
                "model_names": ["model1", "model2", "model3"],
                "agent_kwargs": {"temperature": 0.7},
                "agent_env": {"DEBUG": "true"},
            }],
            "datasets": [[{
                "abbr": "test_dataset",
                "args": {
                    "path": "/path/to/dataset",
                }
            }]],
            "cli_args": {"debug": False}
        })

        task = HarborTask(cfg)
        task.out_detail_dir = os.path.join(self.temp_dir, "test_detail")
        os.makedirs(task.out_detail_dir, exist_ok=True)

        with mock.patch.object(task, '_get_task_count', return_value=1), \
             mock.patch.object(task, '_run_with_tqdm', return_value=(mock.MagicMock(), mock.MagicMock())):
            with mock.patch.object(JobConfig, '__init__', return_value=None):
                with mock.patch.object(AgentName, '__init__', return_value=None):
                    task._run_harbor_job()

    def test_default_fake_api_key(self):
        """Test DEFAULT_FAKE_API_KEY constant"""
        self.assertEqual(DEFAULT_FAKE_API_KEY, "fake_api_key")


if __name__ == '__main__':
    unittest.main()