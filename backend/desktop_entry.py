"""打包入口（PyInstaller / py2app 主脚本）。

源码方式运行请用 `python -m app.desktop`；打包工具以本文件为入口，
由它把控制权交给 app.desktop.main（相对导入需要包上下文，不能直接冻结 app/desktop.py）。
"""
import sys

from app.desktop import main

if __name__ == "__main__":
    sys.exit(main())
