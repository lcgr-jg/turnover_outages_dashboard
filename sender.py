import json
import logging
from typing import Tuple

from confluent_kafka import Producer

logger = logging.getLogger(__name__)


def _ensure_logging_configured():
    """
    Ensure ERROR-level Kafka failures are visible: basicConfig if nothing is set up yet; if the root
    logger would filter INFO, attach a dedicated handler so errors still reach stderr.
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        return
    if logger.handlers:
        return
    if root.isEnabledFor(logging.ERROR):
        return
    h = logging.StreamHandler()
    h.setLevel(logging.ERROR)
    h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [sender] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(h)
    logger.setLevel(logging.ERROR)
    logger.propagate = False


def send_email(recipients,
               subject,
               body,
               from_address='oil.research.desk@jainglobal.com',
               recipients_cc=None,
               topic='fo_tools_alerts',
               attachment={},
               mb_msg_size=15,
               kafka_verbose=False,
               ) -> Tuple[bool, str]:
    """
    Publish one email payload to Kafka. Returns (success, short_detail) for callers to print
    a compact run summary. Success path is silent unless kafka_verbose=True; failures log ERROR.
    """
    _ensure_logging_configured()

    email = {
        "to": recipients.split(','),
        "subject": subject,
        "from_": from_address,
        "body": body,
        "default_footer": False,
    }

    if len(attachment) > 0:
        email["attachment_content"] = attachment['content']
        email["attachment"] = attachment['filename']

    if recipients_cc:
        email["cc"] = recipients_cc.split(',')

    kafka_server = 'kafka1.jainglobal.net:9092,kafka2.jainglobal.net:9092'

    outcome = {}

    def _callback(err, msg):
        if err is not None:
            outcome['err'] = err
            logger.error("Kafka delivery failed: %s", err)
            return
        outcome['topic'] = msg.topic()
        outcome['partition'] = msg.partition()
        try:
            outcome['offset'] = msg.offset()
        except (AttributeError, RuntimeError, ValueError):
            outcome['offset'] = None
        if kafka_verbose:
            logger.info(
                "Kafka delivered topic=%s partition=%s offset=%s",
                outcome['topic'],
                outcome['partition'],
                outcome['offset'],
            )

    producer = Producer({'bootstrap.servers': kafka_server,
                         'message.max.bytes': mb_msg_size * 1024 * 1024})
    producer.produce(topic, value=json.dumps(email), callback=_callback)
    producer.poll(1)
    producer.flush()

    if outcome.get('err') is not None:
        return False, str(outcome['err'])
    if 'partition' not in outcome:
        return False, 'no_delivery_callback'
    off = outcome.get('offset')
    part = outcome.get('partition')
    return True, f"partition={part} offset={off}"
