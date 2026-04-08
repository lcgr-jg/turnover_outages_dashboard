import logging
import json
from confluent_kafka import Producer


def send_email(recipients, 
               subject, 
               body, 
               from_address='jg_research_desk@jainglobal.com', 
               recipients_cc=None, 
               topic='fo_tools_alerts',
               attachment={},
               mb_msg_size=15,
               ):
    
    # Construct dict to send as new Kafka message
    email = {
        "to": recipients.split(','),
        "subject": subject,
        "from_": from_address,
        "body": body,
        "default_footer": False, # removes the "Regards" signature
        }
    
    if len(attachment)>0:
        # Add attachment as a file (content needs to be a base64 str)
        email["attachment_content"] = attachment['content']
        email["attachment"] = attachment['filename']
    
    if recipients_cc:
        email["cc"] = recipients_cc.split(',')
    
    # TODO: will the following change? Shift to config somewhere?
    kafka_server = 'kafka1.jainglobal.net:9092,kafka2.jainglobal.net:9092'
    
    # Create Producer instance  
    producer = Producer({'bootstrap.servers': kafka_server,
                         'message.max.bytes': mb_msg_size * 1024 * 1024})
    # Produce message (to send email)
    producer.produce(topic, value=json.dumps(email), callback=delivery_report)
    # Wait up to 1 second for events for callback to be invoked
    producer.poll(1)
    producer.flush()
    
       
        
def delivery_report(err, msg):
    """
    Delivery report callback.
    """
    if err is not None:
        logging.info(f"Message delivery failed: {err}")
    else:
        logging.info(f"Message delivered to {msg.topic()} [{msg.partition()}]")        




#html_body = generate_report()
#receivers     = 'luiscarlos.gaitan@jainglobal.com'
#email_subject = 'IIR Maintenance Report'

#send_email(receivers,
#           email_subject,
#           html_body,
#           from_address='jg_research_desk@jainglobal.com')