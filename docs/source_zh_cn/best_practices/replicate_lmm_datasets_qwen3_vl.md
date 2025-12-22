# 复现多模态模型数据集评测精度, 以Qwen3-VL-32B为例
## 前言
如果需要复现多模态模型在数据集上的评测精度,主要要对齐如下细节：
### 模型维度
1. 模型任务对齐
2. 最大输出长度
3. 其他后处理参数
### 数据集维度
1. 提示词工程对齐
2. 答案提取方式对齐
3. 评估指标对齐


## 模型任务对齐
多模态模型只能使用vllm_api_general_chat这种任务类型

## 最大输出长度

## 提示词工程相关
在技术报告中检索和mmmu相关的描述
```
B.1 STEM & Puzzle

# MMMU
<image>
Question: {question}
Options:
{options}
Please select the correct answer from the options above.

# MMMUPro_Standard
<image>
{question}
{options}
Please select the correct answer from the options.

# MMMUPro_Vision
<image>
Identify the problem and solve it. Think step by step before answering.


```

#### evalscope使用的提示词工程
https://evalscope.readthedocs.io/en/latest/get_started/supported_dataset/vlm.html#mmmu

```
Solve the following problem step by step. The last line of your response should be of the form "ANSWER: [ANSWER]" (without quotes) where [ANSWER] is the answer to the problem.

{question}

Remember to put your answer on its own line at the end in the form "ANSWER: [ANSWER]" (without quotes) where [ANSWER] is the answer to the problem, and you do not need to use a \boxed command.
```
可以发现相比于AISBench，evalscope在问题之后又重复加了“Remember to put your answer on its own line at the end in the form "ANSWER: [ANSWER]" (without quotes) where [ANSWER] is the answer to the problem, and you do not need to use a \boxed command.“去规范模型的回答格式，这个行为挺奇怪的，但也许有效。

## 参考资料
1. Qwen3-VL-32B-Instruct模型卡片：https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct
2. Qwen3-VL技术报告：https://arxiv.org/pdf/2511.21631
3. Qwen3-VL github主页：https://github.com/QwenLM/Qwen3