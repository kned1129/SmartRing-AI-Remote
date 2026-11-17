# SmartRing AI Remote V0.2.1 — Experimental

ESP32-S3 / VIEWE SmartRing-Plus / 360x360 round touch display + Windows Agent.

## 实际支持

- 全局当前输入框语音；点按开始/结束，可选按住模式；独立Enter/Esc。
- Codex、ChatGPT Classic、GrokBot已运行窗口切换、当前输入框验证。
- Codex本地真实会话和回复只读分页；历史会话ID与电脑窗口的可靠绑定未完成。
- Classic/GrokBot会话列表是未适配，不代表技术上确定无法读取。
- 当前语音后端仅Typeless，通过VB-CABLE桥接；可选择电脑或板载麦克风。
- Windows用户登录自启与退出后重试可选启用。

## 配置边界

进程名/窗口/输入框匹配规则可改。不是任意ASR与任意AI程序路径的即插即用产品；没有通用语音provider加载器或按exe路径启动AI的功能，固件的三应用入口固定。详见[扩展能力核查](docs/开源扩展能力核查-2026-11-16.md)。

`typeless.hotkey`、`virtual_audio.device/host_api`目前没有完整接入，实际仍固定Right Alt和VB-CABLE/WASAPI；修改这些JSON字段不会自动更换实现。请勿据此宣传支持已替换。

## 在自己的电脑安装

1. Windows安装Python 3.12，运行 `powershell -File tools/install.ps1` 创建本目录venv并安装依赖。
2. 安装并自行配置Typeless、VB-CABLE（不随源码提供）。Typeless选CABLE Output；Windows默认设备保持自己的设置。启用自动听写前，在本机确认Right Alt点按切换及Esc取消有效。
3. 运行Configure.cmd，填写自己的电脑LAN IPv4、2.4GHz SSID与密码。本地产生配对令牌、configs/local.json和firmware/main/local_config.h，不提交它们。
4. 在local.json配置adapters；processes填进程文件名，不是完整路径。自行启动目标软件。输入框UIA名称因语言/版本而异，不能任意匹配搜索框或密码框。
5. 需要音频时启用virtual_audio.enabled、typeless.automatic_trigger、typeless.cancel_verified。电脑麦克风转发另启用local_microphone.enabled，device_regex必须唯一匹配实际麦克风。
6. 安装ESP-IDF 5.5.5，在IDF PowerShell终端运行tools/build.ps1；也可传 `-IdfExport <export.ps1路径> -IdfToolsPath <工具目录>`。依赖来自ESP-IDF组件管理器及锁文件，不依赖作者其他工程。
7. 先备份自己的原固件，核对ESP32-S3、16MB Flash及分区，再在生成的构建目录用IDF工具刷入。不要无必要地erase-flash；本源码包不含已配置固件或作者备份。
8. Start.cmd启动；Stop.cmd停止并暂停本次开机自动拉起。可选登录自启：`.venv/Scripts/python.exe tools/manage_autostart.py enable`；取消用disable。

安装路径、音频设备、语言、Wi-Fi地址变化后需在自己的硬件上验证。端口默认8765，用户自行检查LAN连接和防火墙策略。电脑IPv4变动需更新固件配置或自行固定DHCP租约。

## 测试及限制

设置PYTHONPATH=windows-agent，运行 `python -m unittest discover -s tests -v`。自动测试不是第三方电脑/板子的实体兼容性保证。完整长录音压力、休眠恢复、其他ASR及其他AI适配仍需验证。未完成的会话读取不得用示例数据冒充。

可阅读[源码学习笔记](docs/SmartRing%20源码学习笔记.md)。项目代码MIT，字体SIL OFL见NOTICE及字体许可证。源码包排除私人凭据、录音、截图、日志、历史会话及已配置固件。
