"""大模型服务商预设（全部走 OpenAI 兼容接口）。

新增服务商只要在 PRESETS 里加一条即可，不用改调用代码。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    model: str
    key_env: str
    note: str = ""


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="deepseek",
        label="DeepSeek（推荐）",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        key_env="DEEPSEEK_API_KEY",
        note="DeepSeek-V4.1-Flash，中文口语回复效果好、价格低；更强可用 deepseek-v4-pro",
    ),
    ProviderPreset(
        key="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-6-luna",
        key_env="OPENAI_API_KEY",
        note="GPT-6 的轻量版，便宜够用；旗舰可用 gpt-6-astra / gpt-6-sol",
    ),
    ProviderPreset(
        key="dashscope",
        label="阿里百炼 / 通义千问",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.8-flash",
        key_env="DASHSCOPE_API_KEY",
        note="Qwen3.8-Flash，响应快、价格低；更强可用 qwen3.8-max",
    ),
    ProviderPreset(
        key="zhipu",
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-5.3-flash",
        key_env="ZHIPU_API_KEY",
        note="GLM-5.3-Flash，便宜；旗舰可用 glm-5.3",
    ),
    ProviderPreset(
        key="siliconflow",
        label="硅基流动",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen3.6-35B-A3B",
        key_env="SILICONFLOW_API_KEY",
        note="聚合平台；也可选 deepseek-ai/DeepSeek-V4-Flash、zai-org/GLM-5.2",
    ),
    ProviderPreset(
        key="ollama",
        label="本地 Ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3.8:8b",
        key_env="",
        note="本机运行，无需 API Key；显存够可用 qwen3.8:27b",
    ),
    ProviderPreset(
        key="custom",
        label="自定义（OpenAI 兼容）",
        base_url="",
        model="",
        key_env="",
        note="填写任意兼容 /chat/completions 的服务地址",
    ),
)

_BY_KEY = {preset.key: preset for preset in PRESETS}


def get_preset(key: str) -> ProviderPreset | None:
    return _BY_KEY.get(key)


def preset_labels() -> list[str]:
    return [preset.label for preset in PRESETS]


def detect_provider(base_url: str, model: str) -> str:
    """根据 base_url 猜服务商，用于回显当前配置。"""
    base = (base_url or "").rstrip("/")
    for preset in PRESETS:
        if preset.key == "custom" or not preset.base_url:
            continue
        if base.startswith(preset.base_url.rstrip("/")):
            return preset.key
    return "custom"
