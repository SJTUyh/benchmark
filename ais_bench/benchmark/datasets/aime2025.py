'''
Author: SJTUyh yh_silence@alumni.sjtu.edu.cn
Date: 2025-12-03 10:52:54
LastEditors: SJTUyh yh_silence@alumni.sjtu.edu.cn
LastEditTime: 2026-01-29 16:01:25
FilePath: \benchmark\ais_bench\benchmark\datasets\aime2025.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
import json

from datasets import Dataset

from ais_bench.benchmark.registry import LOAD_DATASET
from ais_bench.benchmark.datasets.utils.datasets import get_data_path

from .base import BaseDataset, BaseJDGDatasetMethod


@LOAD_DATASET.register_module()
class Aime2025Dataset(BaseDataset):
    @staticmethod
    def load(path, **kwargs):
        path = get_data_path(path)
        dataset = []
        with open(path, 'r') as f:
            for line in f:
                line = json.loads(line.strip())
                dataset.append(line)
        return Dataset.from_list(dataset)

class Aime2025JDGDataset(Aime2025Dataset):
    @staticmethod
    def load(path, predictions_path, **kwargs):

        dataset_content = super().load(path, **kwargs)

        # 加载被测模型的推理结果
        predictions = BaseJDGDatasetMethod.load_from_predictions(predictions_path, self.abbr)
        # 为数据集添加 model_answer 列
        dataset_list = []
        for i, item in enumerate(dataset_content):
            item_dict = item.copy()
            # 从预测结果中获取模型回答，优先使用 'pred' 字段，其次使用 'prediction' 字段
            model_answer = predictions[i].get("pred", "")
            if not model_answer:
                model_answer = predictions[i].get("prediction", "")
            item_dict["model_answer"] = model_answer
            # 添加 standard_judge_answer 列，使用正则表达式匹配裁判模型的输出（A 或 B）
            item_dict["standard_judge_answer"] = r"^(A|B)$"
            dataset_list.append(item_dict)

        return Dataset.from_list(dataset_list)
