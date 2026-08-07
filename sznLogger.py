import logging
from logging.handlers import RotatingFileHandler
import os
import re
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
    sys.__stderr__.write(f"No se pudo inicializar el archivo de logs en disk: {e}\n")

# Asegurar codificación UTF-8 en streams estándar de Windows (previene UnicodeEncodeError con emojis)
for _stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
    if _stream and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

# Regex compilados una sola vez al importar (rendimiento)
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
_TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')


class DualStreamWriter:
    """Red de seguridad: duplica cualquier print() o excepción residual a
    stdout/stderr hacia logs/tooodles.log con timestamp.

    Tras la migración a logger, solo captura output de librerías externas
    y prints que escapen la migración."""

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
        # 1. Siempre escribir al stream original (consola real)
        try:
            self.original_stream.write(message)
        except Exception:
            pass
        # 2. Escribir al archivo de log con timestamp
        try:
            f = self._get_file()
            clean = _ANSI_RE.sub('', message)

            # Si ya incluye timestamp de logging.Formatter, escribir directo
            if _TIMESTAMP_RE.match(clean):
                f.write(clean)
                self._at_line_start = clean.endswith('\n')
                return

            # Para print() residuales: agregar timestamp al inicio de cada línea
            # Procesamos carácter a carácter para manejar correctamente:
            #   - print("msg") → write("msg") + write("\n") (dos llamadas separadas)
            #   - print("a\nb") → write("a\nb") + write("\n")
            #   - sys.stderr.write("error\n")
            output = []
            for ch in clean:
                if ch == '\n':
                    output.append('\n')
                    self._at_line_start = True
                else:
                    if self._at_line_start:
                        output.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | ")
                        self._at_line_start = False
                    output.append(ch)

            f.write(''.join(output))
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

    def fileno(self):
        """Soporte para librerías que requieren un file descriptor real."""
        return self.original_stream.fileno()

    @property
    def encoding(self):
        return getattr(self.original_stream, 'encoding', 'utf-8')


def enable_stdout_redirection():
    """Activa la duplicación de sys.stdout y sys.stderr hacia logs/tooodles.log."""
    if not isinstance(sys.stdout, DualStreamWriter):
        sys.stdout = DualStreamWriter(sys.__stdout__, LOG_FILE_PATH)
    if not isinstance(sys.stderr, DualStreamWriter):
        sys.stderr = DualStreamWriter(sys.__stderr__, LOG_FILE_PATH)


# Formateadores de texto
CONSOLE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-14s | %(message)s"
FILE_FORMAT    = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
DATE_FORMAT    = "%Y-%m-%d %H:%M:%S"


class ColoredFormatter(logging.Formatter):
    """Formateador con códigos de color ANSI para la consola."""
    COLORS = {
        logging.DEBUG:    "\033[36m",             # Cyan
        logging.INFO:     "\033[32m",             # Verde
        logging.WARNING:  "\033[33m",             # Amarillo
        logging.ERROR:    "\033[31m",             # Rojo
        logging.CRITICAL: "\033[41m\033[37m",     # Rojo fondo / Texto blanco
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        levelname_original = record.levelname
        record.levelname = f"{color}{levelname_original}{self.RESET}"
        formatted = super().format(record)
        record.levelname = levelname_original
        return formatted


class SafeStreamWrapper:
    """Wrapper de seguridad para sys.__stdout__ que reemplaza caracteres no soportados
    en consolas Windows (cp1252/cp850) en lugar de lanzar UnicodeEncodeError."""
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        if not data:
            return
        try:
            self.stream.write(data)
        except UnicodeEncodeError:
            target_enc = getattr(self.stream, 'encoding', None) or 'utf-8'
            safe_str = data.encode(target_enc, errors='replace').decode(target_enc, errors='replace')
            try:
                self.stream.write(safe_str)
            except Exception:
                pass
        except Exception:
            pass

    def flush(self):
        try:
            self.stream.flush()
        except Exception:
            pass


def setup_logger(name: str = "tooodles", log_level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    # 1. Console Handler — usa SafeStreamWrapper(sys.__stdout__) para prevenir UnicodeEncodeError
    console_handler = logging.StreamHandler(SafeStreamWrapper(sys.__stdout__))
    console_handler.setFormatter(ColoredFormatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(console_handler)

    # 2. File Handler (Rotativo: 5 MB por archivo, 3 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(file_handler)

    return logger


# Activar redirección automática al importar sznLogger
enable_stdout_redirection()

# Logger raíz del proyecto
root_logger = setup_logger("tooodles")


def get_logger(submodule: str) -> logging.Logger:
    """Retorna un logger hijo para un subsistema específico (ej: get_logger('recsys')).

    El logger hijo hereda los handlers del logger raíz 'tooodles', por lo que
    automáticamente escribe a consola y archivo con el formato correcto.
    """
    return logging.getLogger(f"tooodles.{submodule}")
