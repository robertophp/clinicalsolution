"""Tests para parseo del webhook Meta (wamid)."""

from backend.services.meta_whatsapp_service import extract_incoming_whatsapp_events


def test_extract_whatsapp_events_includes_wamid_for_text():
    data = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "123"},
                            "messages": [
                                {
                                    "from": "50370123456",
                                    "id": "wamid.HBgLxxxxxxxx",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    events = extract_incoming_whatsapp_events(data)
    assert len(events) == 1
    assert events[0].wamid == "wamid.HBgLxxxxxxxx"
    assert events[0].is_text is True
    assert events[0].text_body == "Hola"


def test_extract_whatsapp_events_includes_wamid_for_media():
    data = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "123"},
                            "messages": [
                                {
                                    "from": "50370123456",
                                    "id": "wamid.MEDIA123",
                                    "type": "image",
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    events = extract_incoming_whatsapp_events(data)
    assert len(events) == 1
    assert events[0].wamid == "wamid.MEDIA123"
    assert events[0].is_text is False
