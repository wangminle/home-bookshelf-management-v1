# 多模态识书评测提示词

把 `tests/eval/covers/` 下的图片逐张读入，只根据画面上的文字作答。不要根据文件名猜。

对每张图输出：

```json
{"title": "书名", "author": "作者", "isbn": null}
```

规则：

- `title`：封面主书名，不含丛书名、宣传语、出版社。
- `author`：封面署名；多人用顿号分隔。看不清则 `null`。
- `isbn`：画面上出现 ISBN/条码数字才填，否则 `null`。不要编造。
- 竖排、艺术字、倾斜、模糊都按所见如实抄录。
- 外文书名与作者保持原文，不要翻译。

填入 `tests/eval/predictions.json` 对应条目的 `predicted` 字段，并填写顶层 `model`（模型名）与 `generated_at`。
