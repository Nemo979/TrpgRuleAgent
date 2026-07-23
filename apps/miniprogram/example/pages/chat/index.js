import { MiniProgramSession } from "../../../lib/session-facade";

Page({
  data: {
    chat: {
      connection: "disconnected",
      connected: false,
      generating: false,
      hasApiKey: false,
      messages: [],
      streamingText: "",
      toolTimeline: [],
      sources: [],
      error: null,
    },
    input: "",
    apiKeyInput: "",
    modelId: "gpt-4o-mini",
    modelBaseUrl: "https://api.openai.com/v1",
    gatewayBaseUrl: "https://gateway.example.com",
  },

  onLoad() {
    this.session = new MiniProgramSession({
      host: { request: (options) => wx.request(options) },
      onState: (state) => this.setData({ chat: state }),
    });
  },

  onHide() {
    // 切后台不持久化 API Key；如产品需要可选择主动停止当前 turn。
  },

  onApiKeyInput(event) {
    this.setData({ apiKeyInput: event.detail.value });
  },

  onGatewayInput(event) {
    this.setData({ gatewayBaseUrl: event.detail.value });
  },

  onModelBaseInput(event) {
    this.setData({ modelBaseUrl: event.detail.value });
  },

  onModelInput(event) {
    this.setData({ modelId: event.detail.value });
  },

  onInput(event) {
    this.setData({ input: event.detail.value });
  },

  async onConnectTap() {
    const ok = await this.session.connect(
      {
        gatewayBaseUrl: this.data.gatewayBaseUrl.trim(),
        modelBaseUrl: this.data.modelBaseUrl.trim(),
        provider: "openai-compatible",
        model: { id: this.data.modelId.trim() || "gpt-4o-mini" },
      },
      this.data.apiKeyInput,
    );
    if (ok) this.setData({ apiKeyInput: "" });
  },

  async onSendTap() {
    const input = this.data.input;
    this.setData({ input: "" });
    await this.session.send(input);
  },

  onStopTap() {
    this.session.stop();
  },

  async onNewSessionTap() {
    await this.session.newSession();
    this.setData({ apiKeyInput: "" });
  },

  onUnload() {
    this.session.destroy();
  },
});
