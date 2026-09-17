import logging
import os

logger = logging.getLogger(__name__)

_firebase_app = None
_firebase_tried = False


def _init_firebase():
    global _firebase_app, _firebase_tried
    if _firebase_tried:
        return _firebase_app
    _firebase_tried = True
    try:
        import firebase_admin
        from firebase_admin import credentials

        cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT")
        if cred_path:
            _firebase_app = firebase_admin.initialize_app(credentials.Certificate(cred_path))
            logger.info("Firebase Auth enabled with service account")
        elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            _firebase_app = firebase_admin.initialize_app()
            logger.info("Firebase Auth enabled via GOOGLE_APPLICATION_CREDENTIALS")
        else:
            logger.info("Firebase not configured; local JWT auth only")
    except Exception as exc:
        logger.warning("firebase-admin unavailable or misconfigured (%s); local JWT auth only", exc)
    return _firebase_app


def verify_firebase_token(token: str):
    # Returns the decoded token dict, or None if verification is not possible/fails.
    try:
        if _init_firebase() is None:
            return None
        from firebase_admin import auth

        return auth.verify_id_token(token)
    except Exception:
        return None
