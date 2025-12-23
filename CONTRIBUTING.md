# Contributing to PulseScribe

Thank you for your interest in contributing to PulseScribe! This document provides guidelines and information for contributors.

## Getting Started

### Prerequisites

- Python 3.10+
- Git
- macOS or Windows (depending on which platform you're developing for)

### Development Setup

```bash
# Clone the repository
git clone https://github.com/KLIEBHAN/pulsescribe.git
cd pulsescribe

# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests to verify setup
pytest -v
```

### Platform-Specific Dependencies

**macOS:**
```bash
brew install portaudio ffmpeg
```

**Windows:**
```bash
pip install pystray pillow pynput pywin32 psutil
pip install PySide6  # Optional, for GPU-accelerated overlay
```

## Development Workflow

### Running the Application

```bash
# macOS
python pulsescribe_daemon.py --debug

# Windows
python pulsescribe_windows.py --debug

# CLI (both platforms)
python transcribe.py --record --copy --mode deepgram
```

### Running Tests

```bash
# Run all tests
pytest -v

# With coverage report
pytest --cov=. --cov-report=term-missing

# Run specific test file
pytest tests/test_providers.py -v
```

### Code Style

- Python 3.10+ type hints (use `|` instead of `Union`)
- No unnecessary abstractions
- Errors → stderr, results → stdout
- German CLI output (target audience)
- Atomic, small commits

## Project Structure

```
pulsescribe/
├── transcribe.py          # CLI orchestration
├── pulsescribe_daemon.py  # macOS daemon
├── pulsescribe_windows.py # Windows daemon
├── config.py              # Central configuration
├── audio/                 # Audio recording
├── providers/             # Transcription providers
├── refine/                # LLM post-processing
├── ui/                    # User interface components
├── whisper_platform/      # Platform abstraction layer
├── utils/                 # Utilities
└── tests/                 # Unit & integration tests
```

See `CLAUDE.md` for detailed architecture documentation.

## Making Changes

### Branching Strategy

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes with atomic commits:
   ```bash
   git commit -m "feat: add new transcription provider"
   git commit -m "fix: correct audio buffer handling"
   ```

3. Push and create a Pull Request

### Commit Message Format

Follow conventional commits:

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `style:` Code style (formatting, no logic change)
- `refactor:` Code refactoring
- `test:` Adding/updating tests
- `build:` Build system or dependencies
- `chore:` Maintenance tasks

Examples:
```
feat: add Groq provider support
fix: handle empty transcription response
docs: update Windows installation guide
refactor: extract audio processing to separate module
```

## Pull Request Guidelines

### Before Submitting

- [ ] All tests pass (`pytest -v`)
- [ ] Code follows project style (type hints, no unnecessary abstractions)
- [ ] New features have tests
- [ ] Documentation updated if needed
- [ ] Commit messages follow conventional format

### PR Description

Include:
1. **Summary:** What does this PR do?
2. **Motivation:** Why is this change needed?
3. **Test plan:** How was this tested?

### Review Process

1. PRs require at least one approval
2. CI must pass (tests, coverage)
3. Address review feedback
4. Squash or rebase before merge (maintain clean history)

## Adding New Features

### New Transcription Provider

1. Create `providers/your_provider.py`
2. Implement the transcription interface
3. Add to `transcribe.py` mode selection
4. Update `README.md` with provider documentation
5. Add tests in `tests/test_providers.py`

### New Platform Support

1. Add platform classes to `whisper_platform/`
2. Update factory functions in `whisper_platform/__init__.py`
3. Create platform-specific daemon if needed
4. Document in `CLAUDE.md` and `README.md`

## Reporting Issues

### Bug Reports

Include:
- PulseScribe version
- Operating system and version
- Steps to reproduce
- Expected vs actual behavior
- Relevant log output (`~/.pulsescribe/logs/pulsescribe.log`)

### Feature Requests

Include:
- Clear description of the feature
- Use case / motivation
- Possible implementation approach (optional)

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow

## Questions?

- Open an issue for questions
- Check existing issues and documentation first
- For security issues, please email directly (do not open public issues)

---

Thank you for contributing to PulseScribe!
