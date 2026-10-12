# 第三方组件许可证核查

本文件记录本项目直接依赖及构建锁定闭包中的第三方组件，用于公开发布前的许可兼容性核查。
版本以 `requirements.lock.txt` 和本地 Python 3.12 构建环境中实际安装的发行元数据为准。

| 组件 | 版本 | 许可证 | 与本项目 MIT 的兼容性 |
|---|---|---|---|
| playwright (Python) | 1.63.0 | Apache License 2.0 | 兼容（宽松许可，可再分发） |
| greenlet | 3.5.6 | MIT | 兼容 |
| pyee | 13.0.1 | MIT | 兼容 |

## 构建期锁定依赖

下列组件用于生成 Windows onedir 发行物，**不作为项目源码的一部分重新发布**；其中
PyInstaller 的引导程序及被收集的运行时文件仍随发行物附带其各自许可证文本。PyInstaller
在其许可证中提供允许构建和分发非 GPL 程序的特别例外。

| 组件 | 版本 | 许可证 / 说明 | 处理结论 |
|---|---:|---|---|
| PyInstaller | 6.22.3 | GPL-2.0-or-later，附特别例外 | 可用于本 MIT 项目的构建与分发；发行前保留其随包许可证 |
| pyinstaller-hooks-contrib | 2026.8 | GPL-2.0-or-later / Apache-2.0（文件类型不同） | 仅构建期；不把其源码作为项目资产再发布 |
| altgraph | 0.17.5 | MIT | 构建期，兼容 |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause | 构建期，兼容 |
| pefile | 2024.8.26 | MIT | 构建期，兼容 |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | 构建期，兼容 |
| setuptools | 84.0.0 | MIT | 构建期，兼容 |
| typing_extensions | 4.16.0 | PSF-2.0 | 构建期，兼容 |

## 其他复用资产

- 浏览器：使用用户本机已安装的 Chrome / Edge，**不随包分发**，因此不涉及再分发许可。
- 官网页面元素与文本：仅由程序在运行时读取，不作为资产打包分发。
- 本项目未包含第三方图标或字体文件。

## 结论

当前运行时直接依赖均为宽松许可证（Apache-2.0 / MIT），与本项目采用的 MIT 许可证兼容。
构建闭包中唯一需要特别说明的是 PyInstaller 及其 hooks：它们只用于构建，且 PyInstaller
提供构建/分发例外；本轮未发现会要求将 TDAC 项目源码改为 GPL 的冲突。若后续新增依赖，
必须先核查其许可证再纳入。

> 注：本核查基于声明的依赖版本；正式发布前应在干净环境用
> `python -m pip install -r requirements.lock.txt` 复核实际解析到的许可证文件。该锁文件
> 固定版本但尚未提供 wheel 哈希；若要抵御下载源替换，公开 CI 应在审阅后补充 `--require-hashes`
> 所需的 wheel SHA-256。
