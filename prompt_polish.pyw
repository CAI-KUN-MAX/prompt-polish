"""Launch PromptPolish without a console window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prompt_polish.app import main  # noqa: E402

if __name__ == "__main__":
    main()
