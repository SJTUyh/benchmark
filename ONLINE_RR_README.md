# 即时调整Request Rate功能使用简介
## 模型配置文件的修改
`vllm_api_stream_chat.py` 中新增了如下配置：
```python
from ais_bench.benchmark.models import VLLMCustomAPIChat
from ais_bench.benchmark.utils.postprocess.model_postprocessors import extract_non_reasoning_content

models = [
    dict(
        attr="service",
        type=VLLMCustomAPIChat,
        abbr="vllm-api-stream-chat",
        path="",
        model="",
        stream=True,
        request_rate=0,
        retry=2,
        api_key="",
        host_ip="localhost",
        host_port=8080,
        url="",
        max_out_len=512,
        batch_size=1,
        trust_remote_code=False,
        token_service_ip="localhost", # 接收request rate的服务的ip
        token_service_port=8888, # 接收request rate的服务的端口
        generation_kwargs=dict(
            temperature=0.01,
            ignore_eos=False,
        ),
        pred_postprocessor=dict(type=extract_non_reasoning_content),
    )
]
```

## 命令启动
ais_bench命令本身不变，但是建议打开debug模型例如
```bash
# 建议打开debug模式，更加实时看到request rate的调整过程
ais_bench --models vllm-api-stream-chat --datasets synthetic_gen_string --mode perf --debug
```

当看到如下进度条日志时，说明可以开始调整request rate了
```
[2025-12-20 19:32:42,086] [ais bench][INF0] Updated request rate from 10 to 20.0
Progress:3%                              256/10000 [00:20<07:58, 20.35case/s]
P0ST=337 (20.0/s)RECV=256 (23.9/s)FAIL=0 (0.0/s)FIN=256 (23.9/s)
```

## 调整request rate
执行工具根目录下的`post_request_rate.py`脚本，设置request rate例如
```bash
python post_request_rate.py --ip localhost --port 8888 --request_rate 20
```
其中`--ip`和`--port`分别是模型配置文件中`token_service_ip`和`token_service_port`的值，默认值分别是`localhost`和`8888`。