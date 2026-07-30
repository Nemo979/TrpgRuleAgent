# 多语言嵌入模型评测计划

当前生产基线是 `BAAI/bge-small-zh-v1.5`，PF1E 85 题混合检索达到 Hit@5 96.5%。重构不会仅凭公开榜单替换模型。

## 第一候选

`intfloat/multilingual-e5-small`：

- MIT 许可证；
- 约 0.1B 参数，384 维；
- 支持中英在内的多语言检索；
- 最大 512 tokens，适合当前约 500 字的子块；
- 检索时必须分别添加 `query:` 和 `passage:` 前缀。

FastEmbed 官方 README 给出了把该模型注册为自定义 ONNX 模型的示例，因此可以继续使用现有 CPU/ONNX 管线。

## 暂不作为默认

- `BAAI/bge-m3`：MIT、多语言、最长 8192 tokens，但 ONNX 权重约 2.29GB，且当前 FastEmbed 稳定支持列表尚未内置它。
- `Alibaba-NLP/gte-multilingual-base`：Apache-2.0、305M 参数、8192 tokens，能力强但依赖自定义模型实现，部署面更重。
- `jinaai/jina-embeddings-v3`：多语言与长文本能力合适，但模型许可证为 CC BY-NC 4.0，不作为项目默认依赖。

## 运行

```bash
bash scripts/benchmark-embedding.sh \
  intfloat/multilingual-e5-small \
  multilingual-e5-small
```

模型只有同时满足以下条件才可替换当前默认值：

1. PF1E 85 题混合检索 Hit@5 不明显低于 96.5%；
2. 中文问题对合成英文 fixture 的跨语言召回通过；
3. 索引大小、构建时间和单次查询延迟适合单容器 CPU 部署；
4. 查询与文档前缀、模型 revision、维度和切块参数写入索引清单。

参考：

- https://huggingface.co/intfloat/multilingual-e5-small
- https://github.com/qdrant/fastembed
- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/Alibaba-NLP/gte-multilingual-base
- https://huggingface.co/jinaai/jina-embeddings-v3
