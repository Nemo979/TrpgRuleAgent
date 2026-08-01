# 夕妖晚谣 1.2 检索验收集

`retrieval.jsonl` 是第二套真实规则库的父文档召回验收集。用例由 PDF
正文人工核对后标注，不包含规则书正文。

运行默认混合检索：

```bash
npm run eval:retrieval:gss
```

运行纯向量检索：

```bash
npm run eval:retrieval:gss:vector
```

数据文件和生成的评测报告位于被 Git 忽略的 `data/`，不会提交规则正文。
