"""apex-memory: a knowledge store for AI agents, served over MCP."""
import importlib.metadata


def package_version() -> str:
    try:
        return importlib.metadata.version('apex-memory')
    except importlib.metadata.PackageNotFoundError:
        return 'unknown'
