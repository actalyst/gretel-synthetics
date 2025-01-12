"""DataSampler module."""
from __future__ import annotations

from typing import List, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from gretel_synthetics.actgan.structures import ColumnIdInfo, SpanInfo


def _is_discrete_column(span_info: List[SpanInfo]) -> bool:
    return len(span_info) == 1 and span_info[0].activation_fn == "softmax"


class DataSampler:
    """DataSampler samples the conditional vector and corresponding data for ACTGAN."""

    def __init__(self, data, output_info: List[List[SpanInfo]], log_frequency):
        self._data = data

        n_discrete_columns = sum(
            [1 for span_info_list in output_info if _is_discrete_column(span_info_list)]
        )

        self._discrete_column_matrix_st = np.zeros(n_discrete_columns, dtype="int32")

        # Store the row id for each category in each discrete column.
        # For example _rid_by_cat_cols[a][b] is a list of all rows with the
        # a-th discrete column equal value b.
        self._rid_by_cat_cols = []

        # Compute _rid_by_cat_cols
        st = 0
        for span_info_list in output_info:
            if _is_discrete_column(span_info_list):
                span_info = span_info_list[0]
                ed = st + span_info.dim

                rid_by_cat = []
                for j in range(span_info.dim):
                    rid_by_cat.append(np.nonzero(data[:, st + j])[0])
                self._rid_by_cat_cols.append(rid_by_cat)
                st = ed
            else:
                st += sum([span_info.dim for span_info in span_info_list])

        assert st == data.shape[1]

        # Prepare an interval matrix for efficiently sample conditional vector
        max_category = max(
            [
                span_info_list[0].dim
                for span_info_list in output_info
                if _is_discrete_column(span_info_list)
            ],
            default=0,
        )

        self._discrete_column_cond_st = np.zeros(n_discrete_columns, dtype="int32")
        self._discrete_column_n_category = np.zeros(n_discrete_columns, dtype="int32")
        self._discrete_column_category_prob = np.zeros(
            (n_discrete_columns, max_category)
        )
        self._n_discrete_columns = n_discrete_columns
        self._n_categories = sum(
            [
                span_info_list[0].dim
                for span_info_list in output_info
                if _is_discrete_column(span_info_list)
            ]
        )

        st = 0
        current_id = 0
        current_cond_st = 0
        for span_info_list in output_info:
            if _is_discrete_column(span_info_list):
                span_info = span_info_list[0]
                ed = st + span_info.dim
                category_freq = np.sum(data[:, st:ed], axis=0)
                if log_frequency:
                    category_freq = np.log(category_freq + 1)
                category_prob = category_freq / np.sum(category_freq)
                self._discrete_column_category_prob[
                    current_id, : span_info.dim
                ] = category_prob
                self._discrete_column_cond_st[current_id] = current_cond_st
                self._discrete_column_n_category[current_id] = span_info.dim
                current_cond_st += span_info.dim
                current_id += 1
                st = ed
            else:
                st += sum([span_info.dim for span_info in span_info_list])

    def _random_choice_prob_index(self, discrete_column_id):
        probs = self._discrete_column_category_prob[discrete_column_id]
        r = np.expand_dims(np.random.rand(probs.shape[0]), axis=1)
        return (probs.cumsum(axis=1) > r).argmax(axis=1)

    def sample_condvec(self, batch):
        """Generate the conditional vector for training.

        Returns:
            cond (batch x #categories):
                The conditional vector.
            mask (batch x #discrete columns):
                A one-hot vector indicating the selected discrete column.
            discrete column id (batch):
                Integer representation of mask.
            category_id_in_col (batch):
                Selected category in the selected discrete column.
        """
        if self._n_discrete_columns == 0:
            return None

        discrete_column_id = np.random.choice(
            np.arange(self._n_discrete_columns), batch
        )

        cond = np.zeros((batch, self._n_categories), dtype="float32")
        mask = np.zeros((batch, self._n_discrete_columns), dtype="float32")
        mask[np.arange(batch), discrete_column_id] = 1
        category_id_in_col = self._random_choice_prob_index(discrete_column_id)
        category_id = (
            self._discrete_column_cond_st[discrete_column_id] + category_id_in_col
        )
        cond[np.arange(batch), category_id] = 1

        return cond, mask, discrete_column_id, category_id_in_col

    def sample_original_condvec(self, batch):
        """Generate the conditional vector for generation use original frequency."""
        if self._n_discrete_columns == 0:
            return None

        cond = np.zeros((batch, self._n_categories), dtype="float32")

        for i in range(batch):
            row_idx = np.random.randint(0, len(self._data))
            col_idx = np.random.randint(0, self._n_discrete_columns)
            matrix_st = self._discrete_column_matrix_st[col_idx]
            matrix_ed = matrix_st + self._discrete_column_n_category[col_idx]
            pick = np.argmax(self._data[row_idx, matrix_st:matrix_ed])
            cond[i, pick + self._discrete_column_cond_st[col_idx]] = 1

        return cond

    def sample_data(self, n, col, opt):
        """Sample data from original training data satisfying the sampled conditional vector.

        Returns:
            n rows of matrix data.
        """
        if col is None:
            idx = np.random.randint(len(self._data), size=n)
            return self._data[idx]

        idx = []
        for c, o in zip(col, opt):
            idx.append(np.random.choice(self._rid_by_cat_cols[c][o]))

        return self._data[idx]

    def dim_cond_vec(self) -> int:
        """Return the total number of categories."""
        return self._n_categories

    def generate_cond_from_condition_column_info(
        self, condition_info: ColumnIdInfo, batch_size: int
    ) -> np.ndarray:
        """Generate the condition vector."""
        vec = np.zeros((batch_size, self._n_categories), dtype="float32")
        id_ = self._discrete_column_matrix_st[condition_info.discrete_column_id]
        id_ += condition_info.value_id
        vec[:, id_] = 1
        return vec
