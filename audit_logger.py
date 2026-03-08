"""
Audit Logger для SABot
Логирование критичных операций для безопасности и compliance
"""
import logging
import json
from datetime import datetime
from typing import Any


class AuditLogger:
    """Структурированное аудит-логирование."""
    
    def __init__(self, log_file: str = "audit.log"):
        self.logger = logging.getLogger("audit")
        self.logger.setLevel(logging.INFO)
        
        # File handler с ротацией (макс 10MB, храним 5 файлов)
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(
            log_file, 
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        
        # Формат: timestamp | user_id | action | details
        formatter = logging.Formatter(
            '%(asctime)s | user_id=%(user_id)s | action=%(action)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        
        # Избегаем дублирования handlers
        if not self.logger.handlers:
            self.logger.addHandler(handler)
    
    def log(self, user_id: int, action: str, details: dict[str, Any] = None):
        """
        Записать аудит-лог
        
        Args:
            user_id: ID пользователя
            action: Тип действия (material_create, user_delete, etc.)
            details: Дополнительные данные (сериализуются в JSON)
        """
        extra = {
            'user_id': user_id,
            'action': action
        }
        
        if details:
            # Фильтруем чувствительные данные
            safe_details = self._sanitize_details(details)
            details_str = json.dumps(safe_details, ensure_ascii=False, default=str)
        else:
            details_str = "{}"
        
        self.logger.info(details_str, extra=extra)
    
    def _sanitize_details(self, details: dict) -> dict:
        """Удалить чувствительные данные из логов."""
        sensitive_keys = ['password', 'token', 'secret', 'api_key', 'credit_card']
        safe = {}
        
        for key, value in details.items():
            if any(s in key.lower() for s in sensitive_keys):
                safe[key] = '[REDACTED]'
            else:
                safe[key] = value
        
        return safe


# Глобальный instance
audit_logger = AuditLogger()


# Удобные функции для типичных операций
def log_material_create(user_id: int, mat_id: int, title: str, stage: str):
    """Лог создания материала."""
    audit_logger.log(user_id, 'material_create', {
        'mat_id': mat_id,
        'title': title,
        'stage': stage
    })


def log_material_delete(user_id: int, mat_id: int, title: str):
    """Лог удаления материала."""
    audit_logger.log(user_id, 'material_delete', {
        'mat_id': mat_id,
        'title': title
    })


def log_material_update(user_id: int, mat_id: int, title: str, changes: dict):
    """Лог обновления материала."""
    audit_logger.log(user_id, 'material_update', {
        'mat_id': mat_id,
        'title': title,
        'changes': changes
    })


def log_event_create(user_id: int, event_id: int, event_type: str, datetime: str):
    """Лог создания события."""
    audit_logger.log(user_id, 'event_create', {
        'event_id': event_id,
        'type': event_type,
        'datetime': datetime
    })


def log_event_delete(user_id: int, event_id: int):
    """Лог удаления события."""
    audit_logger.log(user_id, 'event_delete', {
        'event_id': event_id
    })


def log_role_assign(user_id: int, target_users: list, role: str):
    """Лог назначения ролей."""
    audit_logger.log(user_id, 'role_assign', {
        'target_users': target_users,
        'role': role,
        'count': len(target_users)
    })


def log_user_delete(user_id: int, deleted_user_id: int = None, deleted_username: str = None):
    """Лог удаления пользователя."""
    audit_logger.log(user_id, 'user_delete', {
        'deleted_user_id': deleted_user_id,
        'deleted_username': deleted_username
    })


def log_mentee_status_change(user_id: int, mentorship_id: int, mentee_name: str, 
                             old_status: str, new_status: str):
    """Лог изменения статуса менти."""
    audit_logger.log(user_id, 'mentee_status_change', {
        'mentorship_id': mentorship_id,
        'mentee_name': mentee_name,
        'old_status': old_status,
        'new_status': new_status
    })


def log_mentee_delete(user_id: int, mentorship_id: int, mentee_name: str):
    """Лог удаления менти."""
    audit_logger.log(user_id, 'mentee_delete', {
        'mentorship_id': mentorship_id,
        'mentee_name': mentee_name
    })


def log_mentee_create(user_id: int, mentorship_id: int, mentee_name: str, mentor_id: int):
    """Лог создания менти."""
    audit_logger.log(user_id, 'mentee_create', {
        'mentorship_id': mentorship_id,
        'mentee_name': mentee_name,
        'mentor_id': mentor_id
    })


def log_lion_action(user_id: int, action: str, details: dict = None):
    """Лог действий Льва (высокий уровень привилегий)."""
    audit_logger.log(user_id, f'lion_{action}', details or {})


def log_security_event(user_id: int, event: str, details: dict = None):
    """Лог security-событий (бан, failed attempt, etc)."""
    audit_logger.log(user_id, f'security_{event}', details or {})
