import os

import networkx as nx
import pandas as pd
import cv2
import numpy as np

from scipy.misc import bytescale, imresize


from autocnet.control.control import C
from autocnet.fileio import io_json
from autocnet.fileio.io_gdal import GeoDataset
from autocnet.matcher import feature_extractor as fe # extract features from image
from autocnet.matcher import outlier_detector as od
from autocnet.matcher import subpixel as sp


class CandidateGraph(nx.Graph):
    """
    A NetworkX derived directed graph to store candidate overlap images.

    Parameters
    ----------

    Attributes
    node_counter : int
                   The number of nodes in the graph. 
    node_name_map : dict
                    The mapping of image labels (i.e. file base names) to their
                    corresponding node indices.

    ----------
    """

    def __init__(self,*args, **kwargs):
        super(CandidateGraph, self).__init__(*args, **kwargs)
        self.node_counter = 0
        node_labels = {}
        self.node_name_map = {}

        # the node_name is the relative path for the image
        for node_name, node_attributes in self.nodes_iter(data=True):

            if os.path.isabs(node_name):
                node_attributes['image_name'] = os.path.basename(node_name)
                node_attributes['image_path'] = node_name
            else:
                node_attributes['image_name'] = os.path.basename(os.path.abspath(node_name))
                node_attributes['image_path'] = os.path.abspath(node_name)

            # fill the dictionary used for relabelling nodes with relative path keys
            node_labels[node_name] = self.node_counter
            # fill the dictionary used for mapping base name to node index
            self.node_name_map[node_attributes['image_name']] = self.node_counter
            self.node_counter += 1

        nx.relabel_nodes(self, node_labels, copy=False)

    @classmethod
    def from_adjacency(cls, input_adjacency):
        """
        Instantiate the class using an adjacency dict or file. The input must contain relative or
        absolute paths to image files.

        Parameters
        ----------
        input_adjacency : dict or str
                          An adjacency dictionary or the name of a file containing an adjacency dictionary.

        Returns
        -------
         : object
           A Network graph object

        Examples
        --------
        >>> from autocnet.examples import get_path
        >>> inputfile = get_path('adjacency.json')
        >>> candidate_graph = network.CandidateGraph.from_adjacency(inputfile)
        """
        if not isinstance(input_adjacency, dict):
           input_adjacency = io_json.read_json(input_adjacency)
        return cls(input_adjacency)

    def get_name(self, node_index):
        """
        Get the image name for the given node.

        Parameters
        ----------
        node_index : int
                     The index of the node.
        
        Returns
        -------
         : str
           The name of the image attached to the given node.


        """
        return self.node[node_index]['image_name']

    def get_node(self, node_name):
        """
        Get the node with the given name.

        Parameters
        ----------
        node_name : str
                    The name of the node.
        
        Returns
        -------
         : object
           The node with the given image name.


        """
        return self.node[self.node_name_map[node_name]]

    def get_keypoints(self, nodekey):
        """
        Get the list of keypoints for the given node.
        
        Parameters
        ----------
        nodeIndex : int or string
                    The key for the node, by index or name.
        
        Returns
        -------
         : list
           The list of keypoints for the given node.
        
        """
        if isinstance(nodekey, str):
            return self.get_node(nodekey)['keypoints']
        return self.node[nodekey]['keypoints']

    def add_image(self, *args, **kwargs):
        """
        Adds an image node to the graph.

        Parameters
        ----------

        """

        raise NotImplementedError
        self.add_node(self.node_counter, *args, **kwargs)
        #self.node_labels[self.node[self.node_counter]['image_name']] = self.node_counter
        self.node_counter += 1

    def get_geodataset(self, nodeindex):
        """
        Constructs a GeoDataset object from the given node image and assigns the 
        dataset and its NumPy array to the 'handle' and 'image' node attributes.

        Parameters
        ----------
        nodeindex : int
                    The index of the node.

        """
        self.node[nodeindex]['handle'] = GeoDataset(self.node[nodeindex]['image_path'])

    def get_array(self, nodeindex):
        """
        Downsample the input image file by some amount using bicubic interpolation
        in order to reduce data sizes for visualization and analysis, e.g. feature detection

        Parameters
        ----------
        nodeindex : hashable
                    The index into the node containing a geodataset object

        downsampling : int
                       [1, infinity] downsampling
        """

        array = self.node[nodeindex]['handle'].read_array()

        return bytescale(array)

    def extract_features(self, method='orb', extractor_parameters={}, downsampling=1):
        """
        Extracts features from each image in the graph and uses the result to assign the
        node attributes for 'handle', 'image', 'keypoints', and 'descriptors'.

        Parameters
        ----------
        method : {'orb', 'sift', 'fast'}
                 The descriptor method to be used

        extractor_parameters : dict
                               A dictionary containing OpenCV SIFT parameters names and values.

        downsampling : int
                       The divisor to image_size to down sample the input image.
        """
        for node, attributes in self.nodes_iter(data=True):
            self.get_geodataset(node)
            image = self.get_array(node)
            keypoints, descriptors = fe.extract_features(image,
                                                         method=method,
                                                         extractor_parameters=extractor_parameters)

            attributes['keypoints'] = keypoints
            attributes['descriptors'] = descriptors.astype(np.float32, copy=False)

    def add_matches(self, matches):
        """
        Adds match data to a node and attributes the data to the
        appropriate edges, e.g. if A-B have a match, edge A-B is attributes
        with the pandas dataframe.

        Parameters
        ----------
        source_node : str
                      The identifier for the node

        matches : dataframe
                  The pandas dataframe containing the matches
        """
        source_groups = matches.groupby('source_image')
        for i, source_group in source_groups:
            for j, dest_group in source_group.groupby('destination_image'):
                source_key = dest_group['source_image'].values[0]
                destination_key = dest_group['destination_image'].values[0]
                try:
                    edge = self[source_key][destination_key]
                except: # pragma: no cover
                    edge = self[destination_key][source_key]

                if 'matches' in edge.keys():
                    df = edge['matches']
                    edge['matches'] = pd.concat([df, dest_group])
                else:
                    edge['matches'] = dest_group

    def compute_homographies(self, outlier_algorithm=cv2.RANSAC, clean_keys=[]):
        """
        For each edge in the (sub) graph, compute the homography
        Parameters
        ----------
        outlier_algorithm : object
                            An openCV outlier detections algorithm, e.g. cv2.RANSAC

        clean_keys : list
                     of string keys to masking arrays
                     (created by calling outlier detection)
        Returns
        -------
        transformation_matrix : ndarray
                                The 3x3 transformation matrix

        mask : ndarray
               Boolean array of the outliers
        """

        for source, destination, attributes in self.edges_iter(data=True):
            matches = attributes['matches']

            if clean_keys:
                mask = np.prod([attributes[i] for i in clean_keys], axis=0, dtype=np.bool)
                matches = matches[mask]

                full_mask = np.where(mask == True)

            s_coords = np.empty((len(matches), 2))
            d_coords = np.empty((len(matches), 2))

            for i, (idx, row) in enumerate(matches.iterrows()):
                s_idx = int(row['source_idx'])
                d_idx = int(row['destination_idx'])

                s_coords[i] = self.node[source]['keypoints'][s_idx].pt
                d_coords[i] = self.node[destination]['keypoints'][d_idx].pt

            transformation_matrix, ransac_mask = od.compute_homography(s_coords,
                                                                       d_coords)
            ransac_mask = ransac_mask.ravel()
            # Convert the truncated RANSAC mask back into a full length mask
            if clean_keys:
                mask[full_mask] = ransac_mask
            else:
                mask = ransac_mask

            # Compute the error in the homography
            s_trans = np.hstack((s_coords[::-1],np.ones(s_coords.shape[0]).reshape(-1,1))).T
            s_trans = np.dot(transformation_matrix, s_trans).T

            #Normalize the error
            for i in range(3):
                s_trans[i] /= s_trans[2]

            d_trans = np.hstack((d_coords[::-1], np.ones(d_coords.shape[0]).reshape(-1,1)))

            attributes['homography_error'] = np.sqrt(np.sum((d_trans - s_trans)**2, axis=1))
            attributes['homography'] = transformation_matrix
            attributes['ransac'] = mask

    def compute_subpixel_offsets(self, clean_keys=[], threshold=0.8, upsampling=10,
                                 template_size=9, search_size=27):
        """
        For the entire graph, compute the subpixel offsets using pattern-matching and add the result
        as an attribute to each edge of the graph.

        Parameters
        ----------
        clean_keys : list
             of string keys to masking arrays
             (created by calling outlier detection)

        threshold : float
                    On the range [-1, 1].  Values less than or equal to
                    this threshold are masked and can be considered
                    outliers

        upsampling : int
                     The multiplier to the template and search shapes to upsample
                     for subpixel accuracy

        template_size : int
                        The size of the template in pixels, must be odd

        search_size : int
                      The size of the search
        """

        for source, destination, attributes in self.edges_iter(data=True):
            matches = attributes['matches']

            full_offsets = np.zeros((len(matches), 3))

            # Build up a composite mask from all of the user specified masks
            if clean_keys:
                mask = np.prod([attributes[i] for i in clean_keys], axis=0, dtype=np.bool)
                matches = matches[mask]
                full_mask = np.where(mask == True)

            # Preallocate the numpy array to avoid appending and type conversion
            edge_offsets = np.empty((len(matches),3))

            s_node = self.node[source]
            d_node = self.node[destination]

            s_image = s_node['handle']
            d_image = d_node['handle']

            # for each edge, calculate this for each keypoint pair
            for i, (idx, row) in enumerate(matches.iterrows()):
                s_idx = int(row['source_idx'])
                d_idx = int(row['destination_idx'])

                s_keypoint = [s_node['keypoints'][s_idx].pt[0],
                              s_node['keypoints'][s_idx].pt[1]]

                d_keypoint = [d_node['keypoints'][d_idx].pt[0],
                              d_node['keypoints'][d_idx].pt[1]]

                # Get the template and search windows
                s_template = sp.clip_roi(s_image, s_keypoint, template_size)
                d_search = sp.clip_roi(d_image, d_keypoint, search_size)

                edge_offsets[i] = sp.subpixel_offset(s_template, d_search, upsampling=upsampling)

            # Compute the mask for correlations less than the threshold
            threshold_mask = edge_offsets[edge_offsets[:, -1] >= threshold]

            # Convert the truncated mask back into a full length mask
            if clean_keys:
                mask[full_mask] = threshold_mask
                full_offsets[full_mask] = edge_offsets
            else:
                mask = threshold_mask

            attributes['subpixel_offsets'] = pd.DataFrame(full_offsets, columns=['x_offset',
                                                                                 'y_offset',
                                                                                 'correlation'])
            attributes['subpixel'] = mask

    def to_cnet(self, clean_keys=[]):
        """
        Generate a control network (C) object from a graph

        Parameters
        ----------
        clean_keys : list
             of strings identifying the masking arrays to use, e.g. ratio, symmetry

        Returns
        -------
        merged_cnet : C
                      A control network object
        """

        def _validate_cnet(cnet):
            """
            Once the control network is aggregated from graph edges,
            ensure that a given correspondence in a given image does
            not match multiple correspondences in a different image.

            Parameters
            ----------
            cnet : C
                   control network object

            Returns
            -------
             : C
               the cleaned control network
            """

            mask = np.zeros(len(cnet), dtype=bool)
            counter = 0
            for i, group in cnet.groupby('pid'):
                group_size = len(group)
                if len(group) != len(group['nid'].unique()):
                    mask[counter: counter + group_size] = False
                else:
                    mask[counter: counter + group_size] = True
                counter += group_size

            return cnet[mask]

        merged_cnet = None

        for source, destination, attributes in self.edges_iter(data=True):
            matches = attributes['matches']

            # Merge all of the masks
            if clean_keys:
                mask = np.prod([attributes[i] for i in clean_keys], axis=0, dtype=np.bool)
                matches = matches[mask]

            if 'subpixel' in clean_keys:
                offsets = attributes['subpixel_offsets'][attributes['subpixel']]
            kp1 = self.node[source]['keypoints']
            kp2 = self.node[destination]['keypoints']

            pt_idx = 0
            values = []
            for i, (idx, row) in enumerate(matches.iterrows()):
                # Composite matching key (node_id, point_id)
                m1 = (source, int(row['source_idx']))
                m2 = (destination, int(row['destination_idx']))

                values.append([kp1[m1[1]].pt[0],
                               kp1[m1[1]].pt[1],
                               m1,
                               pt_idx,
                               source])

                kp2x = kp2[m2[1]].pt[0]
                kp2y = kp2[m2[1]].pt[1]

                if 'subpixel' in clean_keys:
                    kp2x += (offsets['x_offset'].values[i])
                    kp2y += (offsets['y_offset'].values[i])
                values.append([kp2x,
                               kp2y,
                               m2,
                               pt_idx,
                               destination])
                pt_idx += 1

            columns = ['x', 'y', 'idx', 'pid', 'nid']

            cnet = C(values, columns=columns)

            if merged_cnet is None:
                merged_cnet = cnet.copy(deep=True)
            else:
                pid_offset = merged_cnet['pid'].max() + 1  # Get the current max point index
                cnet[['pid']] += pid_offset

                # Inner merge on the dataframe identifies common points
                common = pd.merge(merged_cnet, cnet, how='inner', on='idx', left_index=True, suffixes=['_r',
                                                                                                      '_l'])

                # Iterate over the points to be merged and merge them in.
                for i, r in common.iterrows():
                    new_pid = r['pid_r']
                    update_pid = r['pid_l']
                    cnet.loc[cnet['pid'] == update_pid, ['pid']] = new_pid  # Update the point ids

                # Perform the concat
                merged_cnet = pd.concat([merged_cnet, cnet])
                merged_cnet.drop_duplicates(['idx', 'pid'], keep='first', inplace=True)

        # Final validation to remove any correspondence with multiple correspondences in the same image
        merged_cnet = _validate_cnet(merged_cnet)

        return merged_cnet

    def to_json_file(self, outputfile):
        """
        Write the edge structure to a JSON adjacency list

        Parameters
        ==========

        outputfile : str
                     PATH where the JSON will be written
        """
        adjacency_dict = {}
        for n in self.nodes():
            adjacency_dict[n] = self.neighbors(n)
        io_json.write_json(adjacency_dict, outputfile)

    # This could easily be changed to return the image name instead of the node if desired
    def island_nodes(self):
        """
        Finds single nodes that are completely disconnected from the rest of the graph

        Returns
        -------
        : list
          A list of disconnected nodes, nodes of degree zero, island nodes, etc.
        """
        return nx.isolates(self)

    # This could also easily be changed to return image names
    def connected_subgraphs(self):
        """
        Finds and returns a list of each connected subgraph of nodes. Each subgraph is a set.

        Returns
        -------
       : list
          A list of connected sub-graphs of nodes, with the largest sub-graph first. Each subgraph is a set.
        """
        return sorted(nx.connected_components(self), key=len, reverse=True)
