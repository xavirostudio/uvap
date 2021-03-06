# One feature of this demo is that users can replace the person IDs generated by the Reidentifier with real names.
# This is currently done by providing id ==> name associations, which will directly affect the displayed identifier.
# However, the Reidentifier also allows to modify the ids in its internal database, using person streams.
# This feature is not yet exploited in the demo because it would require the user to create feature vectors to be associated with the names.
# The code below contains to-do comments for the next version when this change has been made available.
# These comments are in the form: TODO (when names via person stream)
import argparse
from typing import Dict

import cv2
import numpy as np
from confluent_kafka.cimpl import Producer, Consumer, TopicPartition  # TODO (when names via person stream): remove Consumer and TopicPartition

from utils.kafka.time_ordered_generator_with_timeout import TimeOrderedGeneratorWithTimeout, TopicInfo
from utils.uvap.graphics import draw_nice_bounding_box, draw_overlay, Position, draw_nice_text
from utils.uvap.uvap import message_list_to_frame_structure, encode_image_to_message

COLOR_LIGHT_GREY = (255, 95, 10) # face detected
COLOR_DARK_GREY = (128, 128, 128) # head detected
COLOR_ORANGE = (10, 95, 255) # recognised

# List of topic postfixes to be consumed
TOPIC_POSTFIXES = ["original.Image.jpg", "dets.ObjectDetectionRecord.json", "frameinfo.FrameInfoRecord.json", "fvecs.FeatureVectorRecord.json", "ages.AgeRecord.json"]
REID_TOPIC_POSTFIXES = ["reids.ReidRecord.json"]
OUTPUT_TOPIC_POSTFIX = "reids.Image.jpg"

# List of camera IDs
CAMERA_TOPIC_IDS = ["0", "1"]
# List of Reid topic IDs
REID_TOPIC_IDS = ["99", "100"]


class Registration:
    def __init__(self, id=None, name=None):
        self.id = id
        self.count = 0
        self.age = None
        self.name = name

    def addAge(self, age):
        if self.age:
            # Calculate simple average
            self.age = round((age + self.age * self.count) / (self.count + 1))
        else:
            self.age = age
        self.count += 1

    def addName(self, name):
        self.name = name


def main():
    parser = argparse.ArgumentParser(
        epilog=
        """Description:
           Reidentification demo using any number of cameras: 
           Either camera can be used for registration or reidentification only, or for both.
           
           Plays a video from a jpeg topic,
           visualizes head detection with a gray bounding box around a head.
           When a detection is identified, changes the bounding box color to orange
           and writes the dwell time, age and ID (derived from the reid MS ID) above the heads.
           
           Displays ('-d') or stores ('-o') the result of this demo in kafka topics.

           Required topics (example):
           - <prefix>.cam.0.original.Image.jpg
           - <prefix>.cam.0.dets.ObjectDetectionRecord.json
           - <prefix>.cam.0.frameinfo.FrameInfoRecord.json
           - <prefix>.cam.0.ages.AgeRecord.json
           - <prefix>.cam.1.original.Image.jpg
           - <prefix>.cam.1.dets.ObjectDetectionRecord.json
           - <prefix>.cam.1.frameinfo.FrameInfoRecord.json
           - <prefix>.cam.1.ages.AgeRecord.json
           ...
           - <prefix>.cam.1.reids.ReidRecord.json
           """
        , formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("broker", help="The name of the kafka broker.", type=str)
    parser.add_argument("prefix", help="Prefix of topics (base|skeleton).", type=str)
    parser.add_argument('-d', "--display", action='store_true')
    parser.add_argument('-o', '--output', help='write output image into kafka topic', action='store_true')
    parser.add_argument('text', help='Text to display (age|dwell_time|both).', type=str)
    args = parser.parse_args()

    if not args.display and not args.output:
        parser.error("Missing argument: -d (display output) or -o (write output to kafka) is needed")

    if args.output:
        producer = Producer({'bootstrap.servers': args.broker})

    overlay = cv2.imread('resources/powered_by_white.png', cv2.IMREAD_UNCHANGED)

    # Prepare the topics to read
    input_topics = [f"{args.prefix}.cam.{id}.{topic_postfix}"
        for id in CAMERA_TOPIC_IDS for topic_postfix in TOPIC_POSTFIXES]
    reid_topics = [f"{args.prefix}.cam.{id}.{topic_postfix}"
        for id in REID_TOPIC_IDS for topic_postfix in REID_TOPIC_POSTFIXES]
    consumable_topics = list(map(TopicInfo, input_topics)) \
                        + (list(map(lambda t: TopicInfo(t, drop=False), reid_topics)))

    # TODO (when names via person stream): Remove this consumer
    reg_consumer = Consumer(
        {'bootstrap.servers': args.broker, 'group.id': 'multicamreid_reg', 'auto.offset.reset': 'earliest'})
    reg_consumer.assign([TopicPartition(topic="named.records.json", partition=0, offset=0)])

    output_topics = dict((id, f"{args.prefix}.cam.{id}.{OUTPUT_TOPIC_POSTFIX}") for id in CAMERA_TOPIC_IDS)

    # read message, draw and display them
    consumer = TimeOrderedGeneratorWithTimeout(
        broker=args.broker,
        groupid="detection",
        topics_infos=consumable_topics,
        latency_ms=200,
        commit_interval_sec=None,
        group_by_time=True
    )

    registrations: Dict[str, Registration] = {}
    i = 0
    inner_id = 0
    scaling = 1.0
    for msgs in consumer.getMessages():
        k = -1
        for time, v in message_list_to_frame_structure(msgs).items():
            message = v.get(args.prefix, {})

            # Collect Reid records
            reid_records = {}
            for reid_id in REID_TOPIC_IDS:
                reid_message = message.get(reid_id, {})
                reid_records.update(reid_message.get("reid", {}))

            # Process the image
            for topic_key, topic_message in filter(lambda t: t[0] not in REID_TOPIC_IDS, message.items()):
                img = topic_message.get("image", {})
                if not isinstance(img, np.ndarray):
                    continue
                head_detections = topic_message.get("head_detection", {})
                # Set the image scale
                shape_orig = head_detections.pop("image", {})
                if shape_orig:
                    scaling = img.shape[1] / shape_orig["frame_info"]["columns"]

                # Processing the detections of the image
                for detection_key, detection_record in head_detections.items():
                    object_detection_record = detection_record.get("bounding_box", {})
                    if not object_detection_record:
                        continue
                    key_to_display = ""
                    color = COLOR_DARK_GREY

                    face_detection = detection_record.get("unknown", {})
                    if face_detection:
                        color = COLOR_LIGHT_GREY

                    age = None
                    age_detection_record = detection_record.get("age", {})
                    if age_detection_record:
                        age = age_detection_record["age"]
                    if args.text == "age" or args.text == "both":
                        key_to_display = f"Age: {age}" if age else ""

                    # Reidentification received for the detection
                    reid_records_for_det = reid_records.get(detection_key, {})
                    if reid_records_for_det:
                        for reid_record in filter(lambda r: "reid_event" in r, reid_records_for_det):
                            # We only use the first [0] identified face now
                            reid_key = reid_record["reid_event"]["match_list"][0]["id"]["first_detection_key"]
                            registered = registrations.get(reid_key, None)
                            if registered:
                                age_to_display = ""
                                if age:
                                    registered.addAge(age)
                                if args.text == "age" or args.text == "both":
                                    age_to_display = f"; Age: {registered.age:d}" if age else ""
                                # Calculate the dwell time if required
                                dwell_time_display = ""
                                if args.text == "dwell_time" or args.text == "both":
                                    detection_time = reid_record["reid_event"]["match_list"][0]["id"]["first_detection_time"]
                                    dwell_time = time - int(detection_time)
                                    dwell_time_display = f"; Dwell time: {dwell_time}ms"
                                color = COLOR_ORANGE
                                name_to_display = registered.name if registered.name else f"ID: {registered.id}"
                                key_to_display = f"{name_to_display}{age_to_display}{dwell_time_display}"

                            else:
                                inner_id += 1
                                registrations[reid_key] = Registration(id=inner_id)
                                if age:
                                    registrations[reid_key].addAge(age)

                                # Update the technical naming topic
                                #  TODO (when names via person stream): remove
                                producer.produce("detected.records.json", key=str(reid_key).encode("utf-8"), value=(str(inner_id) + ";").encode("utf-8"), timestamp=time)

                    # Read the technical naming topic
                    #  TODO (when names via person stream): remove
                    reg_msg = reg_consumer.poll(0.01)
                    if reg_msg is not None:
                        try:
                            key = reg_msg.key().decode("utf-8")
                            name = reg_msg.value().decode("utf-8")
                            # Update the person name
                            reg_to_update = registrations.get(key)
                            if reg_to_update:
                                reg_to_update.addName(name)
                            else:
                                registrations[key] = Registration(name=name)
                        except:
                            print("Decoding entry of the named.records topic failed.", flush=True)

                    # draw text above bounding box
                    img = draw_nice_text(
                        canvas=img,
                        text=key_to_display,
                        bounding_box=object_detection_record["bounding_box"],
                        color=color,
                        scale=scaling
                    )

                    # draw bounding_box
                    img = draw_nice_bounding_box(
                        canvas=img,
                        bounding_box=object_detection_record["bounding_box"],
                        color=color,
                        scaling=scaling
                    )

                # draw ultinous logo
                img = draw_overlay(
                    canvas=img,
                    overlay=overlay,
                    position=Position.BOTTOM_RIGHT,
                    scale=scaling
                )

                # produce output topic
                if args.output:
                    out_topic = output_topics.get(topic_key)
                    producer.produce(out_topic, value=encode_image_to_message(img), timestamp=time)
                    producer.poll(0)
                    if i % 1000 == 0:
                        producer.flush()
                    i += 1

                # display #
                if args.display:
                    cv2.imshow(f"DEMO Camera {topic_key}", img)
                    k = cv2.waitKey(33)

        if k == 113:  # The 'q' key to stop
            break
        elif k == -1:  # normally -1 returned,so don't print it
            continue
        else:
            print(f"Press 'q' key for EXIT!")


if __name__ == "__main__":
    main()
