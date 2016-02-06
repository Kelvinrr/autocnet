import cv2
import numpy as np
import pandas as pd


def self_neighbors(matches):
    """
    Returns a pandas data series intended to be used as a mask. Each row
    is True if it is not matched to a point in the same image (good) and
    False if it is (bad.)

    Parameters
    ----------
    matches : dataframe
              the matches dataframe stored along the edge of the graph
              containing matched points with columns containing:
              matched image name, query index, train index, and
              descriptor distance
    Returns
    -------
    : dataseries
      Intended to mask the matches dataframe. True means the row is not matched to a point in the same image
      and false the row is.
    """
    return matches.source_image != matches.destination_image


def distance_ratio(matches, ratio=0.8):
    """
    Compute and return a mask for a matches dataframe
    using Lowe's ratio test.
    Lowe (2004) [Lowe2004]_

    Parameters
    ----------
    matches : dataframe
              the matches dataframe stored along the edge of the graph
              containing matched points with columns containing:
              matched image name, query index, train index, and
              descriptor distance.

    ratio : float
            the ratio between the first and second-best match distances
            for each keypoint to use as a bound for marking the first keypoint
            as "good". Default: 0.8
    Returns
    -------
     mask : ndarray
            Intended to mask the matches dataframe. Rows are True if the associated keypoint passes
            the ratio test and false otherwise. Keypoints without more than one match are True by
            default, since the ratio test will not work for them.

    """

    mask = np.zeros(len(matches), dtype=bool)  # Pre-allocate the mask
    counter = 0
    for i, group in matches.groupby('source_idx'):
        group_size = len(group)
        n_unique = len(group['destination_idx'].unique())
        # If we can not perform the ratio check because all matches are symmetrical
        if n_unique == 1:
            mask[counter:counter + group_size] = True
            counter += group_size
        else:
            # Otherwise, we can perform the ratio test
            sorted_group = group.sort_values(by=['distance'])
            unique = sorted_group['distance'].unique()

            if len(unique) == 1:
                # The distances from the unique points are identical
                mask[counter: counter + group_size] = False
                counter += group_size
            elif unique[1] / unique[0] < ratio:
                # The ratio test passes
                mask[counter] = True
                mask[counter + 1:counter + group_size] = False
                counter += group_size
            else:
                mask[counter: counter + group_size] = False
                counter += group_size

        '''
        # won't work if there's only 1 match for each queryIdx
        if len(group) < 2:
            mask.append(True)
        else:
            if group['distance'].iloc[0] < ratio * group['distance'].iloc[1]: # this means distance _0_ is good and can drop all other distances
                mask.append(True)
                for i in range(len(group['distance']-1)):
                    mask.append(False)
            else:
                for i in range(len(group['distance'])):
                    mask.append(False)
        '''
    return mask


def mirroring_test(matches):
    """
    Compute and return a mask for the matches dataframe on each edge of the graph which
    will keep only entries in which there is both a source -> destination match and a destination ->
    source match.

    Parameters
    ----------
    matches : dataframe
              the matches dataframe stored along the edge of the graph
              containing matched points with columns containing:
              matched image name, query index, train index, and
              descriptor distance

    Returns
    -------
    duplicates : dataseries
                 Intended to mask the matches dataframe. Rows are True if the associated keypoint passes
                 the mirroring test and false otherwise. That is, if 1->2, 2->1, both rows will be True,
                 otherwise, they will be false. Keypoints with only one match will be False. Removes
                 duplicate rows.
    """
    duplicates = matches.duplicated(keep='first').values
    duplicates.astype(bool, copy=False)
    return duplicates


def compute_homography(kp1, kp2, outlier_algorithm=cv2.RANSAC, reproj_threshold=5.0):
    """
    Given two arrays of keypoints compute a homography

    Parameters
    ----------
    kp1 : ndarray
          (n, 2) of coordinates from the source image

    kp2 : ndarray
          (n, 2) of coordinates from the destination image

    outlier_algorithm : object
                        The openCV algorithm to use for outlier detection

    reproj_threshold : float
                       The RANSAC reprojection threshold


    Returns
    -------
    transformation_matrix : ndarray
                            The 3x3 transformation matrix

    mask : ndarray
           Boolean array of the outliers
    """

    transformation_matrix, mask = cv2.findHomography(kp1,
                                                     kp2,
                                                     outlier_algorithm,
                                                     reproj_threshold)
    mask = mask.astype(bool)
    return transformation_matrix, mask

# TODO: CITATION and better design?
def adaptive_non_max_suppression(keypoints, n=100, robust=0.9):
    """
    Select the top n keypoints, using Adaptive Non-Maximal Suppression (see: Brown (2005) [Brown2005]_)
    to rank the keypoints in order of largest minimum suppression
    radius. A mask with only the positions of the top n keypoints set to 1 (and all else set to 0) is returned.

    Parameters
    ----------
    keypoints : list
               List of KeyPoint objects from a node of the graph or equivalently, for 1 image.

    n : int
        The number of top-ranked keypoints to return.

    Returns
    -------
    keypoint_mask : list
                    A list containing a 1 in the positions of the top n selected keypoints and 0 in the positions
                    of all the other keypoints.
    """
    minimum_suppression_radius = {}
    for i, kp1 in enumerate(keypoints):
        x1, y1 = kp1.pt
        temp = []
        for kp2 in keypoints: #includes kp1 for now
            if kp1.response < robust*kp2.response:
                x2, y2 = kp2.pt
                temp.append(np.sqrt((x2-x1)**2 + (y2-y1)**2))
        if(len(temp) > 0):
            minimum_suppression_radius[i] = np.min(np.array(temp))
        else:
            minimum_suppression_radius[i] = np.nan
    df = pd.DataFrame(list(minimum_suppression_radius.items()), columns=['keypoint', 'radius'])
    top_n = df.sort_values(by='radius', ascending=False).head(n)
    temp_df = df.mask(df.radius < top_n.radius.min(), other=np.nan)
    temp_df = temp_df.where(np.isnan(temp_df.keypoint), other=1)
    temp_df = temp_df.mask(np.isnan(temp_df.keypoint), other=0)
    return list(temp_df.radius)





