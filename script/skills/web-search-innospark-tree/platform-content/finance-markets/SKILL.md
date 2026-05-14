# Finance Markets — Layer 3 叶子节点

**工具**：`opencli-rs`（推荐，12x 快）或 `opencli`
需要 Chrome 登录相关平台

```bash
export PATH="$HOME/bin:$PATH"
```

## 雪球（A股/港股/美股）
```bash
opencli-rs xueqiu hot-stock --limit 10
opencli-rs xueqiu stock --symbol SH600519    # 贵州茅台
opencli-rs xueqiu stock --symbol AAPL        # 美股
opencli-rs xueqiu search "比亚迪"
```

## Yahoo Finance
```bash
opencli-rs yahoo-finance quote --symbol AAPL
opencli-rs yahoo-finance quote --symbol BTC-USD
```

## Barchart（期权/期货/资金流向）
```bash
opencli-rs barchart quote --symbol AAPL
opencli-rs barchart options --symbol AAPL
opencli-rs barchart flow --limit 20
```

## 新浪财经
```bash
opencli-rs sinafinance news --limit 10 --type 0    # 全部
opencli-rs sinafinance news --limit 10 --type 1    # A股
opencli-rs sinafinance news --limit 10 --type 2    # 宏观
```

## Bloomberg（新增）
```bash
opencli-rs bloomberg news --limit 10
```

## 什么值得买（新增）
```bash
opencli-rs smzdm hot --limit 10              # 热门好价
opencli-rs smzdm search "关键词"
```

## 通用参数
所有命令支持：`--format table|json|yaml|md|csv`

## 错误处理

| 错误 | 解决 |
|------|------|
| `command not found: opencli-rs` | `export PATH="$HOME/bin:$PATH"` |
| `Extension not connected` | 检查 Chrome + OpenCLI 扩展 |
| 雪球数据为空 | Chrome 中登录 xueqiu.com |
| 降级 | 将 `opencli-rs` 替换为 `opencli`，命令完全相同 |
