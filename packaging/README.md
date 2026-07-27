# 打包成 macOS .app

## 为什么选 PyInstaller，不选 Briefcase

两个都能把 Python 程序打包成 .app，简单对比一下：

|            | PyInstaller                          | Briefcase                              |
|------------|---------------------------------------|-----------------------------------------|
| 适合场景    | "我有个现成脚本/项目，想打个包"        | 从零搭一个要上架 App Store 的正式项目    |
| 对 QtWebEngine 的支持 | 成熟，社区踩过的坑多，hook 现成 | 官方主推 Toga，PySide6 属于"能用但小众"，QtWebEngine 这种大块头资源容易踩坑 |
| 上手成本    | 一个 .spec 文件就够                   | 需要接受它那一套项目结构/生命周期管理     |

Marginalia 目前就是个人日常用的阅读器，不需要上架、不需要自动更新机制，
**PyInstaller 更省事**，所以配置文件按 PyInstaller 来写。如果以后想做成
能分发给别人、走 App Store 或自动更新的正式产品，再迁移到 Briefcase 也不迟。

## 步骤（必须在 macOS 机器上执行）

PyInstaller 不能跨平台打包——在 Linux 上跑，只能打出 Linux 版；
必须在 macOS 上跑这几步，才能拿到 macOS 的 .app。

```bash
cd marginalia          # 项目根目录，也就是 pyproject.toml 所在目录

# 1. 装打包工具（已经加进 pyproject.toml 的 dev 依赖组了）
uv sync --group dev

# 2. 打包
uv run pyinstaller packaging/marginalia.spec --clean

# 3. 产物在这里，双击就能直接打开
open dist/Marginalia.app
```

第一次打包会比较慢（QtWebEngine 本身几百 MB），耐心等。

## 关于「打开时提示"无法验证开发者"」

因为现在没有 Apple 开发者证书签名，第一次双击打开 .app 时，macOS 会拦一下。
自己用的话，解决办法很简单：

- 右键（或按住 Control 点击）Marginalia.app → 选「打开」→ 弹窗里再点一次「打开」
- 之后正常双击就行了，只需要做这一次

如果以后想分发给别人用（而不是自己用），才需要考虑花钱买 Apple 开发者账号、
做代码签名 + 公证（notarization），那是另一个话题，目前不需要。

## 应用图标

`marginalia.spec` 里 `icon=None`，现在用的是系统默认图标。
如果之后有 `.icns` 格式的图标文件，把它放进 `packaging/` 目录，
比如 `packaging/icon.icns`，然后把 spec 里两处 `icon=None` 改成：

```python
icon=str(PROJECT_ROOT / "packaging" / "icon.icns"),
```

（PNG/JPG 不能直接用，得转成 .icns；网上搜"png 转 icns"有很多免费在线工具或
`iconutil` 命令行方法。）

## 已经做的代码改动

打包后，程序不再是"一堆躺在项目目录里的 .py 文件"，而是被解包到一个临时目录里
运行，原来 `ui/main_window.py` 里那种"从 `__file__` 反推项目根目录，再拼
`assets/web/xxx.js`"的写法会失效——反推出来的路径打包后根本不存在。

所以新增了 `core/paths.py`，里面的 `resource_path()` 会区分两种情况：
打包后从 PyInstaller 解压出来的临时目录（`sys._MEIPASS`）里找，
源码直接跑（`uv run main.py`）时还是走原来的相对路径逻辑。
`main_window.py` 里引用 `highlighter.js` / `footnotes.js` 的地方已经改成用它。

`marginalia.spec` 里的 `datas` 那一项，就是负责把 `assets/web/` 整个目录
原样打进 app 包里，跟 `resource_path()` 配合才能找到这些 JS 文件。
