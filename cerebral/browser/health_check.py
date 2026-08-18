"""
Browser service health-checking and auto-restart utilities.
"""
import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Use current working directory as the project root for finding launcher scripts
PROJECT_ROOT = Path.cwd()

BROWSER_SERVICE_URL = "http://localhost:3000"
HEALTH_ENDPOINT = "/health"


async def browser_service_is_running() -> bool:
    """Quickly check if the Playwright browser service is alive via HTTP HEAD."""
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.head(f"{BROWSER_SERVICE_URL}{HEALTH_ENDPOINT}", timeout=3) as resp:
                return resp.status == 200
    except Exception as e:
        logger.debug("[health_check] Health check failed: %s", e)
        return False


def restart_browser_service() -> bool:
    """Attempt to restart the browser service using project launcher scripts."""
    launcher_patterns = ["start-browser.bat", "start-browser.sh", "start-browser"]
    scripts_dir = PROJECT_ROOT / "scripts"
    
    launcher_path: Path | None = None
    
    # Check root for launcher scripts
    for pattern in launcher_patterns:
        p = PROJECT_ROOT / pattern
        if p.exists():
            launcher_path = p
            break
            
    # Check scripts directory
    if not launcher_path and scripts_dir.is_dir():
        for f in scripts_dir.iterdir():
            if f.name in launcher_patterns:
                launcher_path = f
                break
                
    if not launcher_path:
        logger.warning("[health_check] No browser service launcher script found in project root or scripts/ directory.")
        return False
        
    logger.info("[health_check] Restarting browser service via: %s", launcher_path)
    try:
        # Execute launcher via project's standard mechanism
        cmd = ["python", str(launcher_path)] if launcher_path.suffix == ".py" else [str(launcher_path)]
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("[health_check] Browser service restart command executed successfully.")
            return True
        else:
            logger.error("[health_check] Browser service restart command exited with code %d.", result.returncode)
            return False
    except Exception as e:
        logger.error("[health_check] Failed to execute browser service restart: %s", e)
        return False
