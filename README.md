# 泰国电子入境卡（TDAC）辅助填写工具

> **非官方、免费、开源**的泰国电子入境卡（Thailand Digital Arrival Card, TDAC）辅助填表程序。
> 本工具与泰国政府及移民局**无任何隶属关系**。官方入口：<https://tdac.immigration.go.th/arrival-card/>

---

## 中文说明

### 两种用法

- **图形界面（推荐给普通用户）**：双击 `TDAC填写助手.exe`（便携版）或运行 `python tdac_fill.py --gui`，
  打开简体中文界面，按提示录入资料、点按钮即可。
- **命令行（进阶/自动化）**：见下方「常用命令」。

### 这个工具做什么

以你本机已安装的 Chrome/Edge 启动一个**独立用户目录**的可见浏览器，由 Playwright 通过 CDP 接管，自动完成 TDAC 的**预填 + 回读校验**：

1. 读取本机加密保存的个人资料与行程配置；
2. 打开官网并等待人机验证（如需操作由你本人完成）；
3. 自动填写「个人信息」「旅行与住宿」两阶段，并逐项回读比对；
4. 按本地已确认的答案填写健康声明并回读；
5. 到达预览页后输出**脱敏**核对清单，然后**停下**。

### 明确不做

- **不自动提交**、**不自动勾选法律条款**；
- **不绕过**验证码 / 风控 / 官网日期窗口；
- **不猜测**健康声明答案，缺值即停止；
- 不向第三方上传资料；**预览 PDF 默认不生成**。

> **预填完成 ≠ 已成功申报。** 最终阅读条款、勾选、点击 Submit、领取回执，全部由你本人完成。

### 支持范围

- 首版仅支持并承诺测试 **Windows 11 64 位**；不宣称 Windows 10 或其它系统兼容。
- 首版仅覆盖**单人**申报，面向中国普通护照、航空入境、旅游目的、酒店住宿这一常见场景。

### 图形界面（B1）

简体中文界面，使用 Python 标准库 **tkinter**（不引入额外 GUI 框架），功能包括：

- 首次启动录入个人资料、行程与住宿、健康声明；可查看 / 修改 / 删除已保存资料；
- 护照号、手机号、收件邮箱的**格式校验**与**二次确认**；
- 敏感字段**默认以 `*` 遮挡**，只有你主动点击「显示」才会展示完整内容；
- 开始自动填写、查看运行状态、**暂停**、**停止并转人工接管**；
- **本地草稿**：资料没填完也能先「暂存草稿」，下次打开自动恢复继续编辑；
- 界面明确区分「**预填完成**」与「**正式申报成功**」——后者只能由你本人在官网提交后确认。

### 本地草稿暂存与继续填写

允许你**分多次**准备入境信息，不必一次填完：

- **暂存草稿**不做任何必填 / 格式校验，资料不完整也能保存；下次打开程序**自动恢复**并继续编辑；
- 草稿中的护照号、手机号**沿用同一套 Windows DPAPI 加密**（独立的 `secrets/draft-secrets.dat`），
  `draft.json` 内**不含任何敏感明文**，只记录“是否已暂存”；
- 草稿与已确认的正式资料**互相独立**：暂存不会覆盖 `profile.json` / `trip.json` /
  `dynamic.json` / `secrets.dat`；「放弃草稿」可随时回到正式资料；
- **保存草稿与正式校验严格分离**：只有资料完整、格式正确、关键字段经本人二次确认、
  且在申报窗口内，才允许「开始正式预填」；
- **健康声明即使暂存，正式填写前仍须你本人重新确认**（草稿不继承“已确认”标记）；
- 关闭窗口时若有未保存的修改，会提醒你可先暂存草稿，避免资料丢失；
- 首版**只保留一份本机草稿**：不做云同步、不做多草稿、不做账号系统、不引入数据库；
- 草稿文件位于受管数据目录内（`config/draft.json`、`secrets/draft-secrets.dat`），
  会随 `--delete-data` 或界面「删除受管用户资料」一并清理。

### 免安装便携版（B2）

用 PyInstaller **onedir** 方式打包，普通用户**无需安装 Python**：

- 双击 `TDAC填写助手.exe` 即打开图形界面，不需要管理员权限，中文目录可正常启动；
- 使用系统已安装的 Chrome / Edge，**不捆绑浏览器**；
- 个人资料保存在 `%LOCALAPPDATA%\TDAC\`（**程序目录之外**），替换或升级程序不会覆盖；
- 构建脚本：`build_portable.py`（详见脚本头部注释）。

### 隐私与安全相关工具

- **版本检查（B3）**：仅在你在界面点击（或显式运行 `--check-update`）时才联网，只读取
  版本号与官方下载页，**不自动下载、不自动安装、不远程执行代码**。版本来源可配置
  （`config/update.json` 或环境变量 `TDAC_UPDATE_SOURCE`）；仓库尚未建立，因此**未预置任何地址**。
- **脱敏诊断包（B4）**：只包含白名单中的非敏感元信息（版本、平台、布尔存在性等）。
  由你主动导出、可先预览再自行决定是否发送；**不含**姓名、护照、手机号、邮箱、行程、
  浏览器凭据、网页截图或网页源码。导出前会自检，命中敏感模式即拒绝导出。
- **浏览器缺失引导（B6）**：检测不到 Chrome / Edge 时给出中文说明与官方下载入口，
  **不自动安装、不修改系统默认浏览器**。
- **会话与调试端口（B7）**：使用独立用户目录 + 随机**回环**调试端口，启动前做只读残留检测；
  绝不使用会扩大远程调试访问范围的参数（如 `--remote-allow-origins=*`）。

### 当前验证状态（重要）

- **目前只完成离线验证**：本地配置校验、健康缺值即停、预览字段映射、必填清单与日志脱敏，
  纯合成资料下的离线演练（`--dry-run` / `--smoke`）、图形界面与底层配置一致性、
  便携版离线启动与浏览器检测均已通过。
- **真实官网流程尚未验收**：真实日期窗口校验、真实航班号回显、住宿府/区/分区级联与邮编、
  预览页邮箱填写与核对、条款与 Submit 之后的官方回执，都必须等正式申报窗口开放后由本人实测。
- **离线 ≠ 已验收**：上述离线通过只能说明源码逻辑自洽，**不代表**可以在真实官网一次成功。
- 当前版本**不提供**自动化的“官网交互测试模式”。任何会访问真实官网的测试都必须另行单独授权。

### G2 Beta 测试版发布说明

这是 **Beta 测试版**，不是稳定正式版。真实 TDAC 官网完整流程尚未验收；请只在你愿意自行复核并承担人工操作责任的情况下测试。

- 本工具**非泰国政府或移民局官方产品**，免费、开源；当前仅面向 Windows 11 x64 的单人限定场景。
- 每次开始正式预填前，程序都会要求你当次核对关键身份字段、行程住宿与健康声明；任何资料变更都会使本次授权失效。
- 程序不会自动提交、不会自动同意条款，也不会绕过验证码。你必须在官网自行核对、勾选、提交，并保存官方回执。
- Windows 未签名程序可能显示 SmartScreen 或其他安全提示。请仅从项目官方 GitHub Release 下载，并自行核对发布页列出的 SHA-256；PowerShell 示例：`Get-FileHash .\TDAC填写助手-0.1.0-windows-x64.zip -Algorithm SHA256`。
- 发行包命名固定为 `TDAC填写助手-<版本>-windows-x64.zip`，并在同一目录提供 `TDAC填写助手-<版本>-windows-x64.zip.sha256`（内容为 `<SHA-256>  <ZIP 文件名>`，兼容 `sha256sum -c`）。两者由**同一次公开构建**现场产出：ZIP 直接由该次构建的 `TDAC填写助手\` 目录 + `BUILD_INFO.json` + `SHA256SUMS.txt` 打包，打包后再次回读 ZIP 逐文件比对，因此不会出现“ZIP 与构建产物不一致”，也不会把开发机上的旧 ZIP 当成发行物上传。
- ZIP 内部结构固定为 `TDAC填写助手\`（含 EXE 与 `_internal`）、`BUILD_INFO.json`、`SHA256SUMS.txt`，与本地已验证的 ZIP 布局一致。`SHA256SUMS.txt` 覆盖目录内每个文件（外层 ZIP 及其 `.sha256` 校验文件除外）；解压后在该目录执行 `sha256sum -c SHA256SUMS.txt` 可逐文件复核，`BUILD_INFO.json` 中记录应用版本、Python / PyInstaller 版本、EXE 的 SHA-256 与源码清单摘要。
- 解压发行 ZIP 后必须保留整个 `TDAC填写助手` 目录，不能只复制 EXE。护照号/手机号和草稿密文使用 Windows DPAPI；换 Windows 账户或换电脑后无法解密，需要重新录入。

### 数据与隐私

- 用户数据默认位于当前用户的专属数据目录：`%LOCALAPPDATA%\TDAC\`
  （配置 `config/`、密文 `secrets/`、浏览器资料 `browser_profile/`、日志 `logs/`、输出 `output/`）。
- 护照号与手机号使用 **Windows DPAPI** 加密（与当前 Windows 用户 + 本机绑定），换账户或换电脑需重新录入。
- 日志默认脱敏；护照号、手机号、邮箱、航班号、邮编、地址均不写入完整值。
- 图形界面中的敏感字段默认以 `*` 遮挡，只有你主动点击「显示」才会展示完整内容；
  该“主动显示”机制由共享的确认逻辑保证，程序**不会**在后台自动揭示完整值。
- 预览 PDF 仅在你主动选择导出时生成，内容可能含完整个人资料，请按敏感文件保管。
- 删除受管数据：`python tdac_fill.py --delete-data`（需明确确认）。

### 常用命令

```
python tdac_fill.py --gui            # 启动简体中文图形界面（B1）
python tdac_fill.py --check          # 仅做环境与配置检查（不打开浏览器）
python tdac_fill.py --dry-run        # 离线检查：完全合成资料，不启动浏览器、不触网
python tdac_fill.py --smoke          # 同 --dry-run：完全合成资料，不启动浏览器、不触网
python tdac_fill.py                  # 正式流程（受抵达前 3 天窗口限制，会打开官网）
python tdac_fill.py --export-pdf     # 预览校验通过后导出 PDF（可选，含敏感资料）
python tdac_fill.py --session-info   # 只读查看会话与调试端口说明、残留检测（B7）
python tdac_fill.py --export-diagnostic   # 导出脱敏诊断包（B4）
python tdac_fill.py --check-update   # 经你同意后检查可配置的版本来源（B3）
python set_secrets.py                # 录入护照号 / 手机号（加密保存）
python tdac_fill.py --delete-data    # 删除本程序管理的用户数据
```

> `--smoke` 与 `--dry-run` 一律**离线**：使用完全构造的合成资料，不读取真实配置、
> 不启动浏览器、不访问泰国官网，因此**不可能**触发任何正式提交或虚假申报。

### 许可证与安全

- 本项目采用 **MIT** 许可证，详见 [LICENSE](LICENSE)。
- 第三方依赖许可证核查见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
- 安全问题请通过私密渠道报告，详见 [SECURITY.md](SECURITY.md)。
- 本项目为业余维护，不保证修复响应时效。

---

## English Summary

An **unofficial, free, open-source** helper that pre-fills the Thailand Digital
Arrival Card (TDAC) form using a Playwright-controlled Chrome/Edge session on your
own machine. It **never submits**, **never accepts terms**, and **never bypasses**
captchas or the official date window. Sensitive data is stored locally with Windows
DPAPI; the preview PDF is **not generated by default**.

> **Beta notice:** this is not a stable release and the end-to-end flow on the real
> TDAC website has not been accepted. You must review every value, submit it yourself,
> and retain the official receipt.

- **Supported:** Windows 11 x64 only (first release), single traveler.
- **Official site:** <https://tdac.immigration.go.th/arrival-card/>
- **License:** MIT. Third-party license review: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
- **Security reports:** see [SECURITY.md](SECURITY.md).

> Pre-filled ≠ submitted. You must review, accept the terms, submit, and save the
> receipt yourself on the official website.

## 目录结构

```
tdac-open-source/
├─ tdac_app.py             便携版单一入口（双击 → 图形界面；带参数 → 命令行）
├─ tdac_gui.py             简体中文图形界面（tkinter，B1）
├─ tdac_fill.py            主流程：检查、填写、健康、预览、导出
├─ tdac_group.py           团体资料模型（首版不对外承诺多人）
├─ tdac_sec.py             Windows DPAPI 加密资料读写
├─ tdac_paths.py           程序/用户数据目录分离
├─ tdac_privacy.py         日志脱敏 + 关键字段二次确认
├─ tdac_validate.py        护照号 / 手机号 / 邮箱格式校验 + 正式预填门禁
├─ tdac_draft.py           本地草稿暂存（一份草稿；敏感字段走 DPAPI）
├─ tdac_browser.py         浏览器检测、缺失引导、会话与调试端口（B6/B7）
├─ tdac_update.py          可配置版本检查（B3）
├─ tdac_diag.py            脱敏诊断包（B4）
├─ tdac_synth.py           完全合成的测试资料
├─ set_secrets.py          不回显录入护照号/手机号
├─ build_portable.py       PyInstaller onedir 便携版构建脚本（B2）
├─ tools/prepare_release.py         生成干净发布暂存目录（白名单 + 脱敏扫描）
├─ tools/make_release_zip.py        由同次构建产物打包发行 ZIP 并输出其 SHA-256
├─ config/group.example.json        团体配置模板
├─ templates/config/*.example.json  首次运行的用户配置模板
├─ templates/TDAC_同行人必填信息表_template.csv
├─ tests/                  离线单元测试
├─ docs/                   设计/计划文档
├─ requirements.txt        锁定的运行时依赖版本
└─ .github/workflows/build.yml      公开构建工作流
```
