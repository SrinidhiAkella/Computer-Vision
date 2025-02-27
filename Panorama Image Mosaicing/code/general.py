import cv2
import sys
import numpy as np
from ransac import Ransac
import argparse
import os
parser = argparse.ArgumentParser(description= "Image Mosaicing")
parser.add_argument('path_to_directory_containing_images', nargs='+',type=str)
parser.add_argument('-idx', type=int, required=True, help="choose index for the selection of reference image")
parser.add_argument('-mode', type=str, required=True,choices = ['custom-ransac','auto-ransac'], help="select mode")
parser.add_argument('-normalize', type=int, required=True, help="normalization")
args = parser.parse_args()


def ReadImage(ImageFolderPath):
    Images = []

	# Checking if path is of folder.
    if os.path.isdir(ImageFolderPath):  
        ImageNames = os.listdir(ImageFolderPath)
        ImageNames_Split = [[int(os.path.splitext(os.path.basename(ImageName))[0]), ImageName] for ImageName in ImageNames]
        ImageNames_Split = sorted(ImageNames_Split, key=lambda x:x[0])
        ImageNames_Sorted = [ImageNames_Split[i][1] for i in range(len(ImageNames_Split))]
        
        for i in range(len(ImageNames_Sorted)):       
            ImageName = ImageNames_Sorted[i]
            InputImage = cv2.imread(ImageFolderPath + "/" + ImageName)  # Reading images one by one.
            
            # Checking if image is read
            if InputImage is None:
                print("Not able to read image: {}".format(ImageName))
                exit(0)

            Images.append(InputImage)  # Storing images.
            
    else:   # If it is not folder(Invalid Path).
        print("\nEnter valid Image Folder Path.\n")
        
    if len(Images) < 2:
        print("\nNot enough images found. Please provide 2 or more images.\n")
        exit(1)
    
    return Images


def Convert_xy(x, y):
    global center, f

    xt = ( f * np.tan( (x - center[0]) / f ) ) + center[0]
    yt = ( (y - center[1]) / np.cos( (x - center[0]) / f ) ) + center[1]
    
    return xt, yt


def ProjectOntoCylinder(InitialImage):
    global w, h, center, f
    h, w = InitialImage.shape[:2]
    center = [w // 2, h // 2]
    f = 1000    
    
    # Creating a blank transformed image
    TransformedImage = np.zeros(InitialImage.shape, dtype=np.uint8)
    
    # Storing all coordinates of the transformed image in 2 arrays (x and y coordinates)
    AllCoordinates_of_ti =  np.array([np.array([i, j]) for i in range(w) for j in range(h)])
    ti_x = AllCoordinates_of_ti[:, 0]
    ti_y = AllCoordinates_of_ti[:, 1]
    
    # Finding corresponding coordinates of the transformed image in the initial image
    ii_x, ii_y = Convert_xy(ti_x, ti_y)

    # Rounding off the coordinate values to get exact pixel values (top-left corner)
    ii_tl_x = ii_x.astype(int)
    ii_tl_y = ii_y.astype(int)

    # Finding transformed image points whose corresponding 
    # initial image points lies inside the initial image
    GoodIndices = (ii_tl_x >= 0) * (ii_tl_x <= (w-2)) * \
                  (ii_tl_y >= 0) * (ii_tl_y <= (h-2))

    # Removing all the outside points from everywhere
    ti_x = ti_x[GoodIndices]
    ti_y = ti_y[GoodIndices]
    
    ii_x = ii_x[GoodIndices]
    ii_y = ii_y[GoodIndices]

    ii_tl_x = ii_tl_x[GoodIndices]
    ii_tl_y = ii_tl_y[GoodIndices]

    # Bilinear interpolation
    dx = ii_x - ii_tl_x
    dy = ii_y - ii_tl_y

    weight_tl = (1.0 - dx) * (1.0 - dy)
    weight_tr = (dx)       * (1.0 - dy)
    weight_bl = (1.0 - dx) * (dy)
    weight_br = (dx)       * (dy)
    
    TransformedImage[ti_y, ti_x, :] = ( weight_tl[:, None] * InitialImage[ii_tl_y,     ii_tl_x,     :] ) + \
                                      ( weight_tr[:, None] * InitialImage[ii_tl_y,     ii_tl_x + 1, :] ) + \
                                      ( weight_bl[:, None] * InitialImage[ii_tl_y + 1, ii_tl_x,     :] ) + \
                                      ( weight_br[:, None] * InitialImage[ii_tl_y + 1, ii_tl_x + 1, :] )


    # Getting x coorinate to remove black region from right and left in the transformed image
    min_x = min(ti_x)

    # Cropping out the black region from both sides (using symmetricity)
    TransformedImage = TransformedImage[:, min_x : -min_x, :]
    return TransformedImage, ti_x-min_x, ti_y

def FindMatches(BaseImage, SecImage):
    # Using SIFT to find the keypoints and decriptors in the images
    Sift = cv2.SIFT_create()
    BaseImage_kp, BaseImage_des = Sift.detectAndCompute(cv2.cvtColor(BaseImage, cv2.COLOR_BGR2GRAY), None)
    SecImage_kp, SecImage_des = Sift.detectAndCompute(cv2.cvtColor(SecImage, cv2.COLOR_BGR2GRAY), None)

    # Using Brute Force matcher to find matches.
    BF_Matcher = cv2.BFMatcher()
    InitialMatches = BF_Matcher.knnMatch(BaseImage_des, SecImage_des, k=2)

    # Applytng ratio test and filtering out the good matches.
    good = []
    for m, n in InitialMatches:
        if m.distance < 0.75 * n.distance:
            good.append([m])

    return good, BaseImage_kp, SecImage_kp

def get_scaling_value(points):
    mean = np.mean(points,0)
    scale = (points-mean)**2
    scale = 0.5 * np.sum(scale,axis=1)
    scale = np.sqrt(np.mean(scale))
    return scale
    
def normalize_image_points(points):
    """
    Input: 2D list with x,y image points
    Output:
    """
    points = np.array(points) 
    mean = np.mean(points,0)
    # define similarity transformation
    # no rotation, scaling using sdv and setting centroid as origin
    s = get_scaling_value(points)
    T = np.array([[1/s, 0, -(mean[0])/s],
                  [0, 1/s, -(mean[1])/s],
                  [0,   0, 1]])
    points = np.dot(T, np.concatenate((points.T, np.ones((1, points.shape[0])))))

    # retrieve normalized image in the original input shape 
    points = points[0:2].T
    return points, T


def FindHomography(Matches, BaseImage_kp, SecImage_kp):
    # If less than 4 matches found, exit the code.
    if len(Matches) < 4:
        print("\nNot enough matches found between the images.\n")
        exit(0)    

    # Storing coordinates of points corresponding to the matches found in both the images
    BaseImage_pts = []
    SecImage_pts = []
    for Match in Matches:
        BaseImage_pts.append(BaseImage_kp[Match[0].queryIdx].pt)
        SecImage_pts.append(SecImage_kp[Match[0].trainIdx].pt)

    # Changing the datatype to "float32" for finding homography
    BaseImage_pts = np.float32(BaseImage_pts)
    SecImage_pts = np.float32(SecImage_pts)
    if int(args.normalize)==0:        
        # set data points to numpy arrays
        src = np.array(BaseImage_pts)
        dst = np.array(SecImage_pts)
        # Finding the homography matrix(transformation matrix).
        HomographyMatrix, status = cv2.findHomography(SecImage_pts, BaseImage_pts, cv2.RANSAC, 4.0)
        print("Homography Matrix Auto",HomographyMatrix,sep="\n")
        

    elif int(args.normalize)==1:
        src,T1 = normalize_image_points(BaseImage_pts)
        dst,T2 = normalize_image_points(SecImage_pts)
        # Finding the homography matrix(transformation matrix).
        HomographyMatrix, status = cv2.findHomography(SecImage_pts, BaseImage_pts, cv2.RANSAC, 4.0)
        print("Homography Matrix Auto",HomographyMatrix,sep="\n")
    
    else:
        print("Choose normalization values 0 or 1")

    return HomographyMatrix, status

    
def GetNewFrameSizeAndMatrix(HomographyMatrix, Sec_ImageShape, Base_ImageShape):
    # Reading the size of the image
    (Height, Width) = Sec_ImageShape
    
    # Taking the matrix of initial coordinates of the corners of the secondary image
    InitialMatrix = np.array([[0, Width - 1, Width - 1, 0],
                              [0, 0, Height - 1, Height - 1],
                              [1, 1, 1, 1]])
    
    # Finding the final coordinates of the corners of the image after transformation.
    FinalMatrix = np.dot(HomographyMatrix, InitialMatrix)

    [x, y, c] = FinalMatrix
    x = np.divide(x, c)
    y = np.divide(y, c)

    # Finding the dimentions of the stitched image frame and the "Correction" factor
    min_x, max_x = int(round(min(x))), int(round(max(x)))
    min_y, max_y = int(round(min(y))), int(round(max(y)))

    New_Width = max_x
    New_Height = max_y
    Correction = [0, 0]
    if min_x < 0:
        New_Width -= min_x
        Correction[0] = abs(min_x)
    if min_y < 0:
        New_Height -= min_y
        Correction[1] = abs(min_y)
    
    # Again correcting New_Width and New_Height
    # Helpful when secondary image is overlaped on the left hand side of the Base image.
    if New_Width < Base_ImageShape[1] + Correction[0]:
        New_Width = Base_ImageShape[1] + Correction[0]
    if New_Height < Base_ImageShape[0] + Correction[1]:
        New_Height = Base_ImageShape[0] + Correction[1]

    # Finding the coordinates of the corners of the image if they all were within the frame.
    x = np.add(x, Correction[0])
    y = np.add(y, Correction[1])
    OldInitialPoints = np.float32([[0, 0],
                                   [Width - 1, 0],
                                   [Width - 1, Height - 1],
                                   [0, Height - 1]])
    NewFinalPonts = np.float32(np.array([x, y]).transpose())

    # Updating the homography matrix. Done so that now the secondary image completely 
    # lies inside the frame
    HomographyMatrix = cv2.getPerspectiveTransform(OldInitialPoints, NewFinalPonts)
    
    return [New_Height, New_Width], Correction, HomographyMatrix



def StitchImages(BaseImage, SecImage):
    # Applying Cylindrical projection on SecImage
    SecImage_Cyl, mask_x, mask_y = ProjectOntoCylinder(SecImage)

    # Getting SecImage Mask
    SecImage_Mask = np.zeros(SecImage_Cyl.shape, dtype=np.uint8)
    SecImage_Mask[mask_y, mask_x, :] = 255

    # Finding matches between the 2 images and their keypoints
    good, BaseImage_kp, SecImage_kp = FindMatches(BaseImage, SecImage_Cyl)
    
    # Finding homography matrix.
    
    
    Homography_Matrix, Status = FindHomography(good, BaseImage_kp, SecImage_kp)
    # Finding size of new frame of stitched images and updating the homography matrix 
    NewFrameSize, Correction, Homography_Matrix = GetNewFrameSizeAndMatrix(Homography_Matrix, SecImage_Cyl.shape[:2], BaseImage.shape[:2])

    # Finally placing the images upon one another.
    SecImage_Transformed = cv2.warpPerspective(SecImage_Cyl, Homography_Matrix, (NewFrameSize[1], NewFrameSize[0]))
    SecImage_Transformed_Mask = cv2.warpPerspective(SecImage_Mask, Homography_Matrix, (NewFrameSize[1], NewFrameSize[0]))
    BaseImage_Transformed = np.zeros((NewFrameSize[0], NewFrameSize[1], 3), dtype=np.uint8)
    BaseImage_Transformed[Correction[1]:Correction[1]+BaseImage.shape[0], Correction[0]:Correction[0]+BaseImage.shape[1]] = BaseImage

    StitchedImage = cv2.bitwise_or(SecImage_Transformed, cv2.bitwise_and(BaseImage_Transformed, cv2.bitwise_not(SecImage_Transformed_Mask)))

    return StitchedImage


# Main function for performing stitching
def panorama(Images,Reference_image):
    if args.mode == 'custom-ransac':
        print('############ERROR MESSAGE: WE WERE NOT ABLE TO IMPLEMENT THIS TASK THROUGH CUSTOM-RANSAC, PLEASE CHECK "MODE=AUTO-RANSAC" IMPLEMENTATION###########')
        sys.exit()
    last_image = Images[index-1]
    for i in range(len(Images)):
        if i != (index-1):
            StitchedImage = StitchImages(Reference_image, Images[i])
            Reference_image = StitchedImage.copy()     
    StitchedImage = StitchImages(Reference_image, last_image)
    return StitchedImage


imgs_path = "../" + str(args.path_to_directory_containing_images[0])
# Reading images.
Images = ReadImage(imgs_path)
index = (args.idx)
Reference_image, _, _ = ProjectOntoCylinder(Images[index-1])
StitchedImage = panorama(Images,Reference_image)
cv2.imwrite("../results/stitched_panorama.png", StitchedImage)
cv2.namedWindow('stitched_panorama', cv2.WINDOW_NORMAL)
cv2.resizeWindow('stitched_panorama', 640, 480)
cv2.imshow('stitched_panorama', StitchedImage)
cv2.waitKey(0)
        







