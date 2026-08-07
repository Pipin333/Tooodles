import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import time

# Ruta del directorio de logs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(LOGS_DIR, "tooodles.log")

# Asegurar creación del archivo de logs de inmediato al importar
try:
    if not os.path.exists(LOG_FILE_PATH):
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"--- TOODLES LOG INICIADO [{time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
except Exception as e:
    sys.__stderr__.write(f"⚠️ No se pudo inicializar el archivo de logs en disk: {e}\n")

class DualStreamWriter:
    """Duplica automáticamente cualquier print() o excepción lanzada a stdout/stderr en el archivo tooodles.log con fecha y hora."""
    def __init__(self, original_stream, log_file_path):
        self.original_stream = original_stream
        self.log_file_path = log_file_path
        self._file = None
        self._at_line_start = True

    def _get_file(self):
        if self._file is None or self._file.closed:
            self._file = open(self.log_file_path, "a", encoding="utf-8", buffering=1)
        return self._file

    def write(self, message):
        if not message:
            return
        try:
            self.original_stream.write(message)
        except Exception:
            pass
        try:
            f = self._get_file()
            import re
            clean_message = re.sub(r'\x1b\[[0-9;]*m', '', message)

            # Si ya incluye fecha y hora (ej. de logging.Formatter), escribir directamente
            if re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', clean_message):
                f.write(clean_message)
                self._at_line_start = clean_message.endswith('\n')
            else:
                lines = clean_message.split('\n')
                now_str = time.strftime('%Y-%m-%d %H:%M:%S')
                formatted_parts = []
                for idx, line in enumerate(lines):
                    if idx == 0:
                        if self._at_line_start and line.strip():
                            formatted_parts.append(f"{now_str} | {line}")
                        else:
                            formatted_parts.append(line)
                    else:
                        if line.strip():
                            formatted_parts.append(f"{now_str} | {line}")
                        elif idx < len(lines) - 1:
                            formatted_parts.append("")

                f.write('\n'.join(formatted_parts))
                self._at_line_start = clean_message.endswith('\n')

        except Exception:
            pass

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass
        try:
            if self._file and not self._file.closed:
                self._file.flush()
        except Exception:
            pass

def enable_stdout_redirection():
    """Activa la duplicación de sys.stdout y sys.stderr hacia logs/tooodles.log."""
    if not isinstance(sys.stdout, DualStreamWriter):
        sys.stdout = DualStreamWriter(sys.__stdout__, LOG_FILE_PATH)
    if not isinstance(sys.stderr, DualStreamWriter):
        sys.stderr = DualStreamWriter(sys.__stderr__, LOG_FILE_PATH)

# Formateadores de texto
CONSOLE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-14s | %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

class ColoredFormatter(logging.Formatter):
    """Formateador con códigos de color ANSI para la consola."""
    COLORS = {
        logging.DEBUG: "\033[36m",      # Cyan
        logging.INFO: "\033[32m",       # Verde
        logging.WARNING: "\033[33m",    # Amarillo
        logging.ERROR: "\033[31m",      # Rojo
        logging.CRITICAL: "\033[41m\033[37m",  # Rojo fondo / Texto blanco
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        levelname_original = record.levelname
        record.levelname = f"{color}{levelname_original}{self.RESET}"
        formatted = super().format(record)
        record.levelname = levelname_original
        return formatted

def setup_logger(name: str = "tooodles", log_level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    # 1. Console Handler (con colores)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(console_handler)

    # 2. File Handler (Rotativo: 5 MB por archivo, 3 backups)
    file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(file_handler)

    return logger

# Activar redirección automática al importar sznLogger
enable_stdout_redirection()

# Logger raíz
root_logger = setup_logger("tooodles")

def get_logger(submodule: str) -> logging.Logger:
    """Retorna un logger hijo para un subsistema específico (ej: get_logger('recsys'))."""
    return logging.getLogger(f"tooodles.{submodule}")
