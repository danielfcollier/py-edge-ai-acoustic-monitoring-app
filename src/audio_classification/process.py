"""
A module that defines the process for running an audio classification model.

This class inherits from a base model class and provides methods to perform
inference on an audio chunk, filter the results for specific target classes,
and display the scores in a readable format.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2025
"""

from datetime import datetime

import numpy as np

from src.audio_classification.model import AudioClassificationModel


class AudioClassificationProcess(AudioClassificationModel):
    """
    Implements the inference logic for an audio classification model.

    This class provides a high-level interface to take raw audio data,
    generate classification scores, and utility methods to process those scores.
    """

    def __init__(self):
        """
        Initializes the classification process by setting up the base model.
        """
        super().__init__()

    def identify(self, audio_chunk: np.ndarray) -> dict[str, float]:
        """
        Runs inference on a single audio chunk and returns a dictionary of scores.

        :param audio_chunk: A numpy array of audio samples, expected to be 2D
        (samples, channels).
        :return: A dictionary mapping every possible class name to its confidence
        score (0.0 to 1.0).
        """
        scores, _, _ = self._model(np.squeeze(audio_chunk))
        scores = scores.numpy().mean(axis=0)

        return {class_name: scores[index] for index, class_name in enumerate(self._class_names)}

    @staticmethod
    def get_target_scores(
        target_classes: list,
        classification_scores: dict[str, float],
    ) -> dict[str, float]:
        """
        Filters a full dictionary of scores to return only the scores
        for specified target classes.

        This is a utility method to narrow down the results to only the events
        you are interested in (e.g., "Speech", "Dog", "Bark").

        :param target_classes: A list of strings with the class names to keep.
        :param classification_scores: The complete dictionary of scores from
        the identify() method.
        :return: A new dictionary containing only the scores for the target
        classes.
        """
        return {target_class_name: classification_scores[target_class_name] for target_class_name in target_classes}

    @staticmethod
    def show_scores(scores: dict[str, float]):
        """
        Prints a dictionary of scores to the console, prepended with a timestamp
        and formatted for readability. Useful for debugging and real-time monitoring.

        :param scores: A dictionary of class names and their scores.
        """
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        formatted_scores = {key: f"{value:.2f}" for key, value in scores.items()}

        print(f"[{timestamp}] {formatted_scores} [audio-scores]")

    @staticmethod
    def max_score(scores: dict[str, float]) -> tuple[str, float]:
        """
        Finds and returns the single key-value pair with the highest score from
        a dictionary.

        This method is useful for isolating the most likely classification event
        from a model's output. It correctly handles cases where score values might
        be represented as strings.

        :param scores: A dictionary mapping class names (str) to their confidence
        scores (float or str).
        :return: A tuple containing only the single key-value pair with the highest score.
        """
        top_key = max(scores, key=lambda k: float(scores[k]))

        return (top_key, scores[top_key])
