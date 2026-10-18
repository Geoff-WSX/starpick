"""配置加载与校验测试。"""
import pytest

from app.config import Config, ConfigError, load_config, validate


def _good_config() -> Config:
    cfg = Config()
    cfg.categories = {"影视": ["追剧"], "开发": ["CLI 工具"]}
    return cfg


def test_valid_config_passes():
    validate(_good_config())


def test_empty_categories_rejected():
    cfg = Config()
    cfg.categories = {}
    with pytest.raises(ConfigError, match="categories"):
        validate(cfg)


def test_empty_keyword_list_rejected():
    cfg = _good_config()
    cfg.categories = {"影视": []}
    with pytest.raises(ConfigError, match="影视"):
        validate(cfg)


def test_bad_port_rejected():
    cfg = _good_config()
    cfg.port = 99999
    with pytest.raises(ConfigError, match="port"):
        validate(cfg)


def test_bad_token_format_rejected():
    cfg = _good_config()
    cfg.token = "not-a-token"
    with pytest.raises(ConfigError, match="Token"):
        validate(cfg)


def test_good_token_formats_accepted():
    for token in ["ghp_abcdef1234567890", "github_pat_abcdef1234567890"]:
        cfg = _good_config()
        cfg.token = token
        validate(cfg)  # 不应抛错


def test_has_token_raises_search_rate():
    cfg = _good_config()
    assert cfg.search_rate == 10
    cfg.token = "ghp_abcdef1234567890"
    assert cfg.search_rate == 30
    assert cfg.core_rate == 5000


def test_load_config_from_file(tmp_path):
    yaml_text = """
server:
  port: 9000
categories:
  测试类:
    - 关键词A
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.port == 9000
    assert cfg.categories == {"测试类": ["关键词A"]}


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.yaml")
