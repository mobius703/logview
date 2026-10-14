import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from app import read_tail, pick_file


class TailTests(unittest.TestCase):
    def test_native_picker_selection_and_cancel(self):
        import contextlib
        import io
        import json

        for selected in ("D:/任意の場所/server.log", ""):
            root = MagicMock()
            with patch("tkinter.Tk", return_value=root), patch(
                "tkinter.filedialog.askopenfilename", return_value=selected
            ) as dialog, contextlib.redirect_stdout(io.StringIO()) as output:
                pick_file()
            self.assertEqual(json.loads(output.getvalue()), {"path": selected})
            self.assertEqual(dialog.call_args.kwargs["filetypes"][0][1], "*")
            root.destroy.assert_called_once()

    def test_append_truncate_and_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.log"
            path.write_text("一\n二\n三\n", encoding="utf-8")
            self.assertEqual(read_tail(path, 2)["text"], "二\n三")
            with path.open("a", encoding="utf-8") as stream:
                stream.write("四")
            self.assertEqual(read_tail(path, 2)["text"], "三\n四")
            path.write_text("新\n", encoding="utf-8")
            self.assertEqual(read_tail(path)["text"], "新")
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                read_tail(path)
            path.write_text("再作成", encoding="utf-8")
            self.assertEqual(read_tail(path)["text"], "再作成")

    def test_block_boundary_and_encodings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.log"
            for encoding in ("utf-8", "cp932", "utf-16"):
                path.write_text("日本語のログ\n" * 20000, encoding=encoding)
                self.assertEqual(read_tail(path, 100, encoding)["text"], "\n".join(["日本語のログ"] * 100))

    def test_empty_and_invalid_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.log"
            path.touch()
            self.assertEqual(read_tail(path)["lines"], 0)
            with self.assertRaises(ValueError):
                read_tail(path, 0)


if __name__ == "__main__":
    unittest.main()
