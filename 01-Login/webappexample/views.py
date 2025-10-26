import json
import base64
from authlib.jose import jwt
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.shortcuts import redirect, render, redirect
from django.http import HttpResponse
from django.urls import reverse
from urllib.parse import quote_plus, urlencode

oauth = OAuth()

oauth.register(
    "auth0",
    client_id=settings.AUTH0_CLIENT_ID,
    client_secret=settings.AUTH0_CLIENT_SECRET,
    client_kwargs={
        "scope": "openid profile email offline_access",
    },
    server_metadata_url=f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration",
)


def _safe_b64_json(segment):
    """Decode a JWT segment (base64url) into JSON dict or return None.
    Used only for display; signature is not re-validated here because Authlib
    already validated tokens when obtained. Any decode errors are swallowed.
    """
    if not segment:
        return None
    try:
        # Add padding for base64url if needed
        pad = '=' * (-len(segment) % 4)
        data = base64.urlsafe_b64decode(segment + pad)
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


def _decode_id_token(id_token):
    if not id_token:
        return {}
    parts = id_token.split('.')
    header = _safe_b64_json(parts[0]) if len(parts) > 0 else None
    payload = _safe_b64_json(parts[1]) if len(parts) > 1 else None
    # signature (parts[2]) is binary; we don't decode for display beyond length
    sig_len = len(parts[2]) * 3 // 4 if len(parts) > 2 else 0
    return {
        "header": header,
        "payload": payload,
        "signature_bytes_estimate": sig_len,
    }


def index(request):
    token = request.session.get("user") or {}
    id_token = token.get("id_token")
    decoded_id = _decode_id_token(id_token) if id_token else {}
    # Provide a more compact raw token view
    raw_json = json.dumps(token, indent=2) if token else '{}'
    # Derive a display name safely
    display_name = (
        token.get("userinfo", {}).get("nickname")
        or (decoded_id.get("payload") or {}).get("nickname")
        or token.get("userinfo", {}).get("email")
        or (decoded_id.get("payload") or {}).get("email")
        or "User"
    )
    return render(
        request,
        "index.html",
        context={
            "session": token,
            "raw_token_json": raw_json,
            "decoded_id_token": json.dumps(decoded_id, indent=2),
            "id_token": id_token,
            "access_token": token.get("access_token"),
            "display_name": display_name,
        },
    )


def callback(request):
    token = oauth.auth0.authorize_access_token(
        request,
        organization=settings.AUTH0_ORGANIZATION_ID
    )
    request.session["user"] = token
    # If this was initiated from a popup flow, return a minimal page that notifies the opener then closes.
    if request.session.pop("popup_login", None):
        index_url = request.build_absolute_uri(reverse("index"))
        html = f"""
<!DOCTYPE html><html><head><title>Login Complete</title></head>
<body style='font-family:system-ui; margin:2rem; text-align:center; color:#333;'>
    <p>Authentication complete. You can close this window.</p>
    <script>
        (function() {{
            try {{
                if (window.opener) {{
                    window.opener.postMessage({{ type: 'auth0-login-complete' }}, '*');
                    setTimeout(function() {{ window.close(); }}, 10);
                }} else {{
                    window.location = {index_url!r};
                }}
            }} catch(e) {{
                window.location = {index_url!r};
            }}
        }})();
    </script>
</body></html>
"""
        return HttpResponse(html)
    return redirect(request.build_absolute_uri(reverse("index")))


def login(request):
    """Start Auth0 login.

    Enhancements:
    - Accept an email (via POST form field or query param `email`).
    - Pass it to Auth0 using the OpenID Connect `login_hint` param so the
      Universal Login email/identifier field is pre-populated.
    """
    # Accept email from POST (preferred) or fallback to query string
    email = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip() or None
    else:
        email = request.GET.get("email", "").strip() or None

    # Detect popup mode (query or POST) and remember it for callback
    popup_mode = (request.GET.get("popup") or request.POST.get("popup")) == "1"
    if popup_mode:
        request.session["popup_login"] = True

    callback_url = settings.AUTH0_CALLBACK_URL or request.build_absolute_uri(
        reverse("callback")
    )

    extra_params = {
        "organization": settings.AUTH0_ORGANIZATION_ID
    }
    if email:
        extra_params["login_hint"] = email
        # Optionally store for later UX usage (not required for Auth0 itself)
        request.session["prefill_email"] = email

    return oauth.auth0.authorize_redirect(request, callback_url, **extra_params)


def logout(request):
    request.session.clear()

    return redirect(
        f"https://{settings.AUTH0_DOMAIN}/v2/logout?"
        + urlencode(
            {
                "returnTo": request.build_absolute_uri(reverse("index")),
                "client_id": settings.AUTH0_CLIENT_ID,
            },
            quote_via=quote_plus,
        ),
    )
