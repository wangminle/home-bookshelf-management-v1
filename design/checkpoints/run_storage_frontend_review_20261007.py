"""在临时目录运行历史复核探针，不修改源码或真实数据。

探针多数断言本报告记录的错误行为：通过表示问题已复现，不表示修复通过。
需要已有 frontend/node_modules 和 node；修复后需调整正式回归断言。
"""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    frontend = root / "frontend"
    node = shutil.which("node")
    if node is None or not (frontend / "node_modules/vitest/vitest.mjs").is_file():
        raise SystemExit("需要 node 及已安装的 frontend/node_modules")
    case_dir = Path(tempfile.mkdtemp(prefix="hbs-storage-review-")).resolve()
    (case_dir / "node_modules").symlink_to(frontend / "node_modules", target_is_directory=True)
    shutil.copyfile(Path(__file__).with_name("repro_storage_frontend_review_20261007.test.ts"),
                    case_dir / "probe.test.ts")
    config = (
        "import {defineConfig} from 'vite';\n"
        "import vue from '@vitejs/plugin-vue';\n"
        "export default defineConfig({root:" + json.dumps(str(case_dir)) +
        ",plugins:[vue()],define:{__APP_VERSION__:JSON.stringify('0.4.2')},"
        "resolve:{alias:{'@':" + json.dumps(str(frontend / "src")) + "}},"
        "test:{globals:true,environment:'jsdom',include:['probe.test.ts']}});\n"
    )
    (case_dir / "vitest.config.ts").write_text(config)
    print(f"诊断目录：{case_dir}", flush=True)
    print("注意：通过表示历史错误行为复现或正确行为确认，请按测试名称区分。", flush=True)
    return subprocess.run(
        [node, str(frontend / "node_modules/vitest/vitest.mjs"),
         "run", "--config", str(case_dir / "vitest.config.ts")], cwd=case_dir,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
