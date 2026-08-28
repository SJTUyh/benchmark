# custom_dataset_generator —— 自由生成 ais_bench 自定义数据集

## 简介

该工具用于按 [自定义数据集使用说明](../../docs/source_zh_cn/advanced_tutorials/custom_dataset.md) 中定义的格式，**自由生成** ais_bench 可用的自定义数据集（选择题 `mcq` / 问答题 `qa`，格式 `.jsonl` / `.csv`）：

- 每条 case 的输入（`question`）字符**随机生成**；
- 输入字符个数遵循**泊松分布**（`--input-lambda` 控制均值）；
- 每条 case 的 `max_tokens` 遵循**泊松分布**（`--max-token-lambda` 控制均值）；
- 支持 `ascii` / `alnum` / `letter` / `digit` / `chinese` 及自定义字符集；
- 支持设置随机种子（`--seed`），保证生成结果可复现。

## 依赖与环境

- Python3 + numpy（用于泊松分布采样；可复用项目已有的 Python 环境）。
- 无需修改 ais_bench 源码，生成的数据集可直接通过文档中的命令行或配置文件方式使用。

## 快速开始

在 `benchmark/` 根目录下运行：

```bash
# 生成 1000 条 mcq 数据（4 个选项），输入长度均值 256、max_tokens 均值 512
python tools/custom_dataset_generator/generate_custom_dataset.py \
    --output-path test_mcq.jsonl --data-type mcq --num-cases 1000 \
    --input-lambda 256 --max-token-lambda 512 --option-count 4

# 生成 2000 条 qa 数据，csv 格式，中文字符输入，固定随机种子
python tools/custom_dataset_generator/generate_custom_dataset.py \
    --output-path test_qa.csv --data-type qa --num-cases 2000 \
    --input-lambda 128 --max-token-lambda 1024 --charset chinese --seed 42

# qa 类型不生成 answer 字段（数据集无正确答案）
python tools/custom_dataset_generator/generate_custom_dataset.py \
    --output-path test_qa.jsonl --data-type qa --num-cases 500 \
    --input-lambda 64 --max-token-lambda 256 --no-answer
```

输出示例（mcq / jsonl）：

```json
{"question": "P>i%tiY}(+'-Wm<QVJr_-9M;ZQQvi0[g>;A8p+%Hg}Q[#,+sISVeZo)#", "A": "Uw7zu4weDXlCiQD", "B": "oIP6Bj", "C": "NRgtVZAX0y3roQ", "D": "iZmXaXWWPDR", "answer": "B", "max_tokens": 127}
```

输出示例（qa / csv）：

```bash
question,max_tokens
絫趛鉜恷劍暝敠閡飕乮盻酗壇轜埃瑞郳曙樂擔裿拦鼷牿电睟緄筣矆龡逺軿蝬脄槶鼘琻徧鍌嬣鑏耺坨冚牸僭妛砸鶏琺遊餵醆膘爱砨揕皾洛手齲价嗸巆鵵蚿陏平褞汍瘙乎肣鈐葬媪秅揱鴟阯嵅矍鬏,262
```

## 命令行参数

| 参数 | 类型/默认 | 说明 |
|---|---|---|
| `--output-path` | str，必填 | 输出数据集文件路径（`.jsonl` / `.csv`），默认根据扩展名推断格式 |
| `--format` | `jsonl`/`csv`，可选 | 输出格式；扩展名无法推断时需显式指定 |
| `--data-type` | `mcq`/`qa`，必填 | 数据类型：选择题 / 问答题 |
| `--num-cases` | int，默认 100 | 生成的 case 条数 |
| `--input-lambda` | float，默认 128 | 输入字符个数泊松分布均值 |
| `--input-min` | int，默认 1 | 输入字符个数下限（采样结果截断到该值） |
| `--input-max` | int，默认 `input_lambda*4+10` | 输入字符个数上限（采样结果截断到该值） |
| `--max-token-lambda` | float，默认 512 | `max_tokens` 泊松分布均值 |
| `--max-token-min` | int，默认 1 | `max_tokens` 下限（采样结果截断到该值） |
| `--max-token-max` | int，默认 `max_token_lambda*4+16` | `max_tokens` 上限（采样结果截断到该值） |
| `--charset` | str，默认 `ascii` | `question` 随机字符集：预置 `ascii`/`alnum`/`letter`/`digit`/`chinese`，或直接传任意自定义字符串 |
| `--option-count` | int，默认 4 | mcq 选项个数（范围 2~26，从 `A` 开始生成连续选项字母） |
| `--option-max-len` | int，默认 16 | mcq 选项值与 qa 答案的最大随机长度（实际长度在 `[1, option_max_len]` 内均匀取值） |
| `--no-answer` | 开关 | qa 类型不生成 `answer` 字段（数据集无正确答案） |
| `--no-max-token` | 开关 | 不生成 `max_tokens` 字段 |
| `--seed` | int，可选 | 随机种子，设置后生成结果可复现 |

## 生成规则说明

- **泊松分布 + 截断**：`question` 长度与 `max_tokens` 先按对应 lambda 的泊松分布采样，再截断到 `[min, max]` 区间（防止极端长尾），运行结束时会打印实际均值/最小/最大值，便于核对分布是否符合预期。
- **mcq 字段**：`question, A, B, ..., answer, [max_tokens]`。选项从 `A` 开始生成连续的单个大写字母；`answer` 随机取其中一个选项字母（与文档一致：答案必须是所用选项之一）。
- **qa 字段**：`question, [answer], [max_tokens]`。默认生成 `answer`，`--no-answer` 可省略（对应文档"该数据集无正确答案"场景）。
- **`max_tokens` 语义**：以「每条请求」为粒度写入数据集文件，与文档 [特殊字段](#最大输出长度max_tokens) 一致。由于已逐条写入，**无需**再生成 `.meta.json` 文件（`.meta.json` 仅用于未在数据中指定 max_tokens 或需数据采样等性能测评场景）。

## 与 ais_bench 对接

生成的数据集可直接按文档方式使用，无需修改源码。

### 命令行方式

```bash
ais_bench \
    --models vllm_api_general \
    --custom-dataset-path test_mcq.jsonl \
    --custom-dataset-data-type mcq \
    --mode all
```

```bash
ais_bench \
    --models mindie_stream_api_general \
    --custom-dataset-path test_qa.jsonl \
    --custom-dataset-data-type qa \
    --custom-dataset-infer-method gen
```

### 配置文件方式（精度测评）

```python
datasets = [
    ...,
    {"path": "test_mcq.jsonl", "data_type": "mcq", "infer_method": "gen"},
    ...,
]
```

## 注意事项

- 生成的是**随机文本**数据，仅用于压测/流程验证，不保证语义正确性；精度测评场景请使用真实数据集。
- CSV 中 `question` 含逗号/引号时由 Python `csv` 模块自动转义，可正常被 ais_bench 加载。
- 同一命令重复运行（不指定 `--seed`）会得到不同数据；指定 `--seed` 可复现。
