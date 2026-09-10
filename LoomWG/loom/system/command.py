import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a system command."""

    command: list[str]
    return_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.return_code == 0


class CommandRunner:
    """Execute system commands."""

    def run(
        self,
        command: list[str],
        timeout: int = 30,
    ) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

            return CommandResult(
                command=command,
                return_code=result.returncode,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
            )

        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                return_code=-1,
                stdout="",
                stderr="Command timed out",
            )

        except OSError as error:
            return CommandResult(
                command=command,
                return_code=-1,
                stdout="",
                stderr=str(error),
            )