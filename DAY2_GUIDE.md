# 《蛋仔派对》玩家反馈项目：第二天执行指南

第二天目标：使用大模型对近期 500 条评论完成结构化分析，进行独立人工评估，留下可复查的错误、使用量和运营分析底表。

今天只使用这一个主数据文件：

```text
data/eggy_party_appstore_cn_recent_500.json
```

本项目只维护这一套 500 条分析数据。

## 今天完成后应有的文件

```text
runs/day2_dev_v1/                 10 条开发试跑
runs/day2_full_v1/results.json    500 条最终状态
runs/day2_full_v1/results.jsonl   每次调用的追加日志
runs/day2_full_v1/report/         汇总 CSV 和运营摘要
runs/day2_full_v1/qa_sample.csv   30 条结果人工检查表
runs/day2_full_v1/evaluation.json 50 条盲标评估结果
runs/day2_full_v1/evaluation.json.confusion.csv  分类混淆表
```

## 第一步：先完成 50 条人工盲标

在调用模型、查看模型分类之前，打开：

```text
data/day1_split_recent_500/blind_labels.csv
```

只填写三列：

- `gold_category`：人工判断的主类别。
- `gold_sentiment`：人工判断的情绪。
- `notes`：歧义、多诉求或判断依据，可选。

`gold_category` 只能填写以下十个类别之一：

```text
BUG
数值平衡
玩法体验
性能优化
视听表现
付费体验
账号服务
运营服务
社区生态
其他
```

`gold_sentiment` 只能填写：

```text
正向
中性
负向
```

一条评论出现多个问题时，按照以下顺序选择主问题：

1. 无法进入游戏、进度损失或交易权益受损。
2. 评论者篇幅最多、语气最强调的具体问题。
3. 无法判断具体问题时选择“其他”，并在 notes 说明原因。

用 Excel 保存时选择 CSV UTF-8。不要修改 `review_id` 和 `text`，也不要先查看模型输出。这个顺序决定了最后的准确率是否可信。

## 第二步：准备 DeepSeek API

本项目使用兼容 Chat Completions 的 HTTPS 接口，不需要安装额外 Python 库。推荐配置：

```text
Base URL: https://api.deepseek.com
Model: deepseek-v4-flash
Thinking: disabled
Response format: json_object
```

本任务是单条文本分类，使用 `deepseek-v4-flash` 并关闭 thinking，以控制延迟和输出长度。

在 DeepSeek 平台创建 API Key 并确保账户有可用余额。不要把 Key 写入 `main.py`、Prompt、截图、README 或 Git。

官方文档：

- 首次 API 调用：https://api-docs.deepseek.com/quick_start/pricing-details-cny/
- JSON 输出：https://api-docs.deepseek.com/guides/json_mode/
- Chat Completions：https://api-docs.deepseek.com/api/create-chat-completion/
- 当前模型与价格：https://api-docs.deepseek.com/quick_start/pricing/

## 第三步：运行 10 条开发试验

在项目文件夹打开 PowerShell：

```powershell
python --version
python -m unittest -v
powershell -ExecutionPolicy Bypass -File .\day2.ps1 -Step test -RunVersion v1
```

脚本会隐藏输入 API Key，将它只放在当前进程的环境变量中，然后完成：

```text
读取 30 条开发样本
调用模型分析前 10 条
校验 JSON 字段和枚举
校验 evidence 是否为原文片段
生成试跑明细和摘要
```

查看：

```text
runs/day2_dev_v1/report/details.csv
runs/day2_dev_v1/report/failures.csv
```

逐条检查以下内容：

| 检查项 | 正确标准 |
|---|---|
| 主类别 | 对应评论最重要的问题 |
| sentiment | 反讽不能误判为正向；正负混合以主诉求为准 |
| intensity | 0–3，非负向必须为 0 |
| impact | 只有阻断游玩、进度损失或交易权益受损才轻易使用 3 |
| evidence | 必须逐字出现在评论原文中 |
| suggestion | 是调查或验证动作，不直接承诺修复、补偿或上线日期 |
| needs_review | 多诉求、信息不足、反讽和影响不清时应为 true |

不要因为一条偶然误判就无限增加规则。只有看到重复出现的同类错误时，才修改 `prompt.txt` 中对应的定义或增加一个代表性示例。

如果修改了 Prompt，换一个运行版本重新试跑：

```powershell
powershell -ExecutionPolicy Bypass -File .\day2.ps1 -Step test -RunVersion v2
```

不要复用 `day2_dev_v1`，代码会检测 Prompt 和运行配置变化。

## 第四步：全量分析近期 500 条

确认试跑结果合理后，用最终 Prompt 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\day2.ps1 -Step full -RunVersion v1
```

如果最终 Prompt 是在 `v2` 试跑中确定的，全量目录也可命名为 v2：

```powershell
powershell -ExecutionPolicy Bypass -File .\day2.ps1 -Step full -RunVersion v2
```

每处理一条，终端会显示评论 ID 和 `ok`/`error`。结果逐条追加到 `results.jsonl`，中断后使用相同命令即可续跑；已成功的评论不会再次收费调用，失败项会重新尝试。

运行结束后检查 `report/summary.json`：

```json
{
  "total": 500,
  "analyzed": "应尽量接近500",
  "errors": "需要检查",
  "pending": "正常完成后应为0",
  "negative_rate": "仅在成功分析评论中计算",
  "coverage": "analyzed / 500",
  "needs_review": "待人工复核数量",
  "usage": {
    "prompt_tokens": "API返回的成功调用输入量",
    "completion_tokens": "API返回的成功调用输出量",
    "total_tokens": "两者合计"
  }
}
```

如果 `errors` 不为 0，先查看 `report/failures.csv`，修正 API 配置问题后再次执行相同 full 命令。不要手工删除成功结果。

API 账单以服务商后台为准。日志中的 usage 只汇总成功且返回 usage 的调用，无法覆盖所有失败或服务商侧重试费用。

## 第五步：抽查 30 条模型结果

全量运行完成后：

```powershell
powershell -ExecutionPolicy Bypass -File .\day2.ps1 -Step qa -RunVersion v1
```

打开：

```text
runs/day2_full_v1/qa_sample.csv
```

人工填写：

```text
category_correct：是 / 否
sentiment_correct：是 / 否
score_reasonable：是 / 否
suggestion_useful：是 / 否
human_notes：错误原因或修改建议
```

这 30 条用于检查影响程度、证据和建议质量。它和 50 条盲标准确率的用途不同，不要把两者混成一个数字。

## 第六步：计算盲标评估结果

确认 50 条人工标签全部填写后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\day2.ps1 -Step evaluate -RunVersion v1
```

生成：

```text
runs/day2_full_v1/evaluation.json
runs/day2_full_v1/evaluation.json.confusion.csv
```

重点报告四个值：

```text
category_accuracy        成功预测中的主类别准确率
sentiment_accuracy       成功预测中的情绪准确率
coverage                 50 条盲标中成功得到模型预测的比例
end_to_end_category_success  将 API 失败也计入分母的分类成功率
```

简历中不能只写准确率而省略样本量和覆盖率。合适的写法是：

```text
在50条独立人工盲标样本上评估主类别识别，分类准确率为[真实结果]，预测覆盖率为[真实结果]。
```

## 不使用脚本时的底层命令

如果不想运行 `day2.ps1`，可以在同一个 PowerShell 窗口手工执行：

```powershell
$secret = Read-Host '输入 DeepSeek API Key' -AsSecureString
$env:LLM_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
$env:LLM_BASE_URL = 'https://api.deepseek.com'
$env:LLM_MODEL = 'deepseek-v4-flash'
$env:LLM_THINKING = 'disabled'

python main.py analyze --input data/day1_split_recent_500/dev_sample.json --out runs/day2_dev_v1 --limit 10
python main.py report --input runs/day2_dev_v1/results.json --out runs/day2_dev_v1/report

python main.py analyze --input data/eggy_party_appstore_cn_recent_500.json --out runs/day2_full_v1 --limit 500
python main.py report --input runs/day2_full_v1/results.json --out runs/day2_full_v1/report

python main.py qa-sample --input runs/day2_full_v1/results.json --limit 30 --out runs/day2_full_v1/qa_sample.csv
python main.py evaluate --input runs/day2_full_v1/results.json --labels data/day1_split_recent_500/blind_labels.csv --out runs/day2_full_v1/evaluation.json
```

## 第二天下班前检查

- 50 条人工标签在看模型结果前完成。
- 最终运行只使用近期 500 条数据。
- 每条结果都包含原文证据和可执行建议。
- `total = analyzed + errors + pending`。
- failures 和 review_queue 已人工查看。
- 30 条质量抽查已填写。
- 评估同时报告准确率、样本量和覆盖率。
- Prompt 最终版本和错误案例已保存。
- 没有把模型建议描述成已执行的运营决策。
