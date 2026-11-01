# 英语录音来源

- 全部录音由 `tools/english_audio.py` 根据英语题库里的 `audioText` 合成，不是真人录音。
- 语音合成模型：[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)（Apache-2.0 许可），美式英语女声 `af_heart`。
- 单词语速 0.85、句子语速 0.9；单声道 24 kHz、48 kbps mp3。
- 字母 A、he、she、I'm 在脚本里固定了发音（`PRONOUNCE`），避免单独朗读时读成弱读。
