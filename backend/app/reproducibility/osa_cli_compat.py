from __future__ import annotations

from urllib.parse import urlparse


def _patch_sourcecraft_metadata_loader() -> bool:
    """Supply the SourceCraft metadata loader when OSA does not expose it."""
    import osa_tool.core.git.git_agent as git_agent

    if hasattr(git_agent, "SourceCraftMetadataLoader"):
        return False

    from osa_tool.core.git.metadata import SourceCraftMetadataLoader

    git_agent.SourceCraftMetadataLoader = SourceCraftMetadataLoader
    return True


def _patch_sourcecraft_public_clone_url() -> bool:
    """Use SourceCraft's git host for unauthenticated/public clones.

    When SourceCraftAgent inherits the generic public URL builder, it targets
    the web host instead of git.sourcecraft.dev. Use the git host without a PAT,
    while preserving any SourceCraft-specific public URL implementation.
    """
    import osa_tool.core.git.git_agent as git_agent

    sourcecraft_agent = git_agent.SourceCraftAgent
    if "_get_unauth_url" in sourcecraft_agent.__dict__:
        # Preserve an explicit SourceCraft implementation supplied by OSA.
        return False

    def _get_unauth_url(self, url: str | None = None) -> str:
        repo_url = (url or self.repo_url).strip()
        parsed = urlparse(repo_url)
        host = (parsed.hostname or "").lower()
        if host in {"sourcecraft.dev", "git.sourcecraft.dev"}:
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
                repo = repo[:-4] if repo.lower().endswith(".git") else repo
                return f"https://git@git.sourcecraft.dev/{owner}/{repo}.git"
        return git_agent.GitAgent._get_unauth_url(self, url)

    sourcecraft_agent._get_unauth_url = _get_unauth_url
    return True


def main() -> int:
    patched_loader = _patch_sourcecraft_metadata_loader()
    patched_clone = _patch_sourcecraft_public_clone_url()
    if patched_loader or patched_clone:
        applied = []
        if patched_loader:
            applied.append("metadata-loader import")
        if patched_clone:
            applied.append("public clone URL")
        print(f"[OSA.Edu] Applied SourceCraft compatibility patch: {', '.join(applied)}.", flush=True)

    from osa_tool.run import main as osa_main

    return int(osa_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
