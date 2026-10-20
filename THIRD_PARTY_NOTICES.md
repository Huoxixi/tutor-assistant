# 第三方技术、数据与许可证

应用依赖版本来自 2026-10-19 本机已安装发行包元数据，不表示最新版本；完整环境快照见 [requirements-lock.txt](requirements-lock.txt)。源码仓库不捆绑第三方 Python 包；Windows 运行包捆绑所需依赖和 Python，附实际版本清单与可取得的许可文本于 `third_party_licenses/`。构建工具固定 PyInstaller 6.22.3，配置见 `requirements-build.txt`。

## 核心组件

| 组件 / 已验证版本 | 用途 | 许可与来源 |
| --- | --- | --- |
| Python 3.13.14 | 语言与标准库 | [PSF](https://docs.python.org/3/license.html) |
| FastAPI 0.143.3 | 本地 API | [MIT](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| Uvicorn 0.54.0 | ASGI 服务 | [BSD-3-Clause](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) |
| Pydantic 2.14.1 | 数据校验 | [MIT](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| Starlette 1.8.0 | FastAPI 的 ASGI 基础 | [BSD-3-Clause](https://github.com/Kludex/starlette/blob/main/LICENSE.md) |
| HTTPX 0.28.1 | 高德接口 HTTP 客户端 | [BSD-3-Clause](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| SQLite（标准库使用） | 本地数据存储 | [Public Domain](https://www.sqlite.org/copyright.html) |
| pytest 9.1.1 | 测试 | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |

## Windows 微信读取依赖

| 组件 / 已验证版本 | 用途 | 发行包标注许可与来源 |
| --- | --- | --- |
| pywechat127 1.9.8 | 提供 `pyweixin` 导航模块 | [PyPI 1.9.8：LGPLv3](https://pypi.org/project/pywechat127/1.9.8/)；[上游仓库](https://github.com/Hello-Mr-Crab/pywechat) |
| pywinauto 0.6.9 | Windows UI Automation | [BSD 3-clause](https://github.com/pywinauto/pywinauto/blob/master/LICENSE) |
| pywin32 312 | COM / Win32 / DPAPI 支持 | [元数据为 PSF；组件许可见上游各目录](https://github.com/mhammond/pywin32) |
| comtypes 1.4.17 | COM 互操作 | [MIT](https://github.com/enthought/comtypes/blob/master/LICENSE.txt) |
| psutil 7.2.2 | 适配包的进程支持 | [BSD-3-Clause](https://github.com/giampaolo/psutil/blob/master/LICENSE) |
| PyAutoGUI 0.9.54 | 适配包的桌面操作依赖 | [BSD](https://github.com/asweigart/pyautogui/blob/master/LICENSE.txt) |
| pyperclip 1.11.0 | 适配包的剪贴板依赖 | [BSD](https://github.com/asweigart/pyperclip/blob/master/LICENSE.txt) |

本项目固定使用 `pywechat127==1.9.8`，通过正常 Python 导入其 `pyweixin` 模块，不复制第三方包源码到 Git 仓库、不修改其实现。运行包将未修改的模块以 `.py` 文件放在 `_internal/pyweixin/`，可替换为兼容修改版，不将它封入 PYZ。精确版本的 PyPI 源码归档与 SHA-256、发行包元数据、LGPLv3 / GPLv3 及 LGPL2.1 文本附在 `third_party_licenses/`，源码归档也单独提供为 Release 附件。对应应用源码、构建脚本和打包配置随 Git 标签公开，不限制为调试此类修改而进行的逆向工程。历史遍历、消息边界、时间解析与本地订单识别位于本项目代码中。

**版本许可区别：** 1.9.8 发行包的 License classifier 为 LGPLv3，License 字段为 LGPL；2026-10-19 核对的[上游主分支 LICENSE](https://github.com/Hello-Mr-Crab/pywechat/blob/main/LICENSE) 则为 LGPL 2.1 文本。保留这一区别和两份原文，不将本项目 MIT 声明当作微信组件的许可证。

上述清单涵盖主要组件及部分传递依赖，不替代每个发行物自带的许可文件。Windows UI Automation、COM 和 DPAPI 属于系统能力，微信客户端属于独立第三方产品，均不随本仓库分发。

运行包还包括微信组件导入所需的 NumPy、Pillow、sounddevice、soundfile 及传递依赖。未使用的 ASIO 驱动不分发。soundfile 所载 libsndfile 1.2.2 DLL 保持独立、可替换；包内及 Release 附件另有其上游 1.2.2 源码归档和校验和，按 LGPL2.1 分别保留许可。Tcl/Tk 运行时、PyInstaller bootloader 许可及其他依赖通知保留在包内对应目录和 `third_party_licenses/`。

## 内置地区数据：Vant Area Data

- 来源：[youzan/vant · packages/vant-area-data](https://github.com/youzan/vant/tree/main/packages/vant-area-data)。
- 许可：[MIT](https://github.com/youzan/vant/blob/main/LICENSE)，原文完整保留在 [app/assets/regions-LICENSE.txt](app/assets/regions-LICENSE.txt)。
- 归属：Youzan、Chen Jiahan 及其他贡献者，以所保留许可原文为准。
- 本地处理：`tools/build_regions.py` 将 `app/assets/regions-source.ts` 转为 `app/assets/regions.json`，用于省、市、区县选择及本地匹配。
- 数据记录获取日期为 2026-10-18；JSON 保留来源、许可、日期与源文件哈希。这是项目自带快照，不是实时行政区划服务。
- 本项目未使用 Vant 前端组件库，仅使用上述地区数据。

## 可选外部服务：高德开放平台

通勤功能通过 HTTPX 请求高德的地理编码和路径规划接口，无需安装高德 SDK，未引入新的 Python 依赖。高德在线服务及其地图数据不属于本项目开源代码，受[高德服务协议](https://lbs.amap.com/pages/terms/)及[配额与计费规则](https://lbs.amap.com/upgrade)约束，不能因本项目采用 MIT 而视为高德服务免费或可自由分发。

接口依据：[地理编码](https://lbs.amap.com/api/webservice/guide/api/georegeo)、[路径规划](https://lbs.amap.com/api/webservice/guide/api/direction)。所有真实调用均需使用者配置自己的 Web 服务 Key 并单独授权发送位置。

## 本项目许可证

原创代码采用 [MIT License](LICENSE)，版权所有者为 Huoxixi。第三方代码、数据及权利人声明独立保留；本项目许可证不改变第三方许可。
