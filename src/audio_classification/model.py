"""
A module that defines the base class for loading the YAMNet audio
classification model and its corresponding class map from local files.

This class serves as a foundation for other processes that need to perform
inference, ensuring that the model is loaded only once.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2025
"""

import csv
import os

import tensorflow_hub as hub

# --- Constants ---

# The base directory where the YAMNet model assets are stored locally.
YAMNET_PATH = ".yamnet"


class AudioClassificationModel:
    """
    Handles the loading of the YAMNet model and its class map from a local directory.
    This acts as a base class for processes that will use the model for inference.
    """

    def __init__(self):
        """
        Initializes the model loader. This constructor orchestrates the loading
        of both the TensorFlow Hub model and the CSV class map.
        """
        print("\nLoading YAMNet model and class map...")
        model_path = os.path.join(YAMNET_PATH, "model")
        class_map_path = os.path.join(YAMNET_PATH, "class_map", "yamnet_class_map.csv")

        self._model = self._load_model(model_path)
        self._class_names = self._load_class_names(class_map_path)

    def _load_model(self, model_path: str):
        """
        Loads the TensorFlow Hub model from a specified local path.

        :param model_path: The file path to the directory containing the saved model.
        :return: The loaded TensorFlow Hub model object.
        """
        yamnet_model = hub.load(model_path)
        print("✅ YAMNet model loaded.")
        return yamnet_model

    def _load_class_names(self, file_path: str) -> list[str]:
        """
        Loads the human-readable class names from the YAMNet class map CSV file.

        :param file_path: The path to the 'yamnet_class_map.csv' file.
        :return: A list of class names, converted to lowercase.
        :raises SystemExit: If the class map file is not found.
        """
        class_names = []
        try:
            with open(file_path) as csv_file:
                reader = csv.reader(csv_file)
                print(reader)
                next(reader)
                # | | display_name |
                class_names = [row[2] for row in reader]

        except FileNotFoundError:
            print(f"❌ FATAL: YAMNet class map not found at file {file_path}.")
            exit()

        print("✅ YAMNet class map loaded.")
        return [class_name.lower() for class_name in class_names]
