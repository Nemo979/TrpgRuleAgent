/**
 * 零依赖 SSE 解析器。
 *
 * 输入任意切分的字节流，产出每个事件的 data 载荷字符串：
 * - 行可以跨 chunk 到达；
 * - 一个 chunk 可以包含多行、多个事件；
 * - 支持 \n 与 \r\n 行结束符；
 * - 同一事件的多个 data: 行按 SSE 规范用 \n 连接；
 * - 注释行（: 开头）与 event:/id:/retry: 字段被忽略；
 * - 流结束时残留的 data 行会被冲刷（宽松处理，兼容缺少结尾空行的服务端）。
 */
export async function* parseSseStream(
  source: AsyncIterable<Uint8Array>,
): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  let buffer = "";
  let dataLines: string[] = [];

  const takeEvent = (): string | undefined => {
    if (dataLines.length === 0) {
      return undefined;
    }
    const payload = dataLines.join("\n");
    dataLines = [];
    return payload;
  };

  const handleLine = (rawLine: string): string | undefined => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      return takeEvent();
    }
    if (line.startsWith(":")) {
      return undefined;
    }
    if (line.startsWith("data:")) {
      let value = line.slice(5);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
      dataLines.push(value);
    }
    // 其他字段（event:/id:/retry:）当前无需处理。
    return undefined;
  };

  for await (const chunk of source) {
    buffer += decoder.decode(chunk, { stream: true });
    let newlineIndex: number;
    while ((newlineIndex = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      const payload = handleLine(line);
      if (payload !== undefined) {
        yield payload;
      }
    }
  }

  buffer += decoder.decode();
  if (buffer.length > 0) {
    const payload = handleLine(buffer);
    if (payload !== undefined) {
      yield payload;
    }
  }
  const finalPayload = takeEvent();
  if (finalPayload !== undefined) {
    yield finalPayload;
  }
}
