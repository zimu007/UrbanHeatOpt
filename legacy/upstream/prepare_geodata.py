from ast import Return
import pandas as pd
import geopandas as gpd
import osmnx as ox
from termcolor import cprint
import math
from numpy import random
import os
import shapely
from legacy.upstream.data import *
#import fiona
from pyproj import CRS, Transformer
from shapely.geometry import Point
from shapely.ops import transform
from shapely.geometry import Polygon


def get_geodata_from_place(place_name: str) -> gpd.GeoDataFrame:
    """Get geodata from a place name using the OSMnx library.

    This function retrieves geodata from a place name using the OSMnx library. The data is filtered for buildings.

    :param place_name: Location/Place name to get the geodata for. The name of the place must be a valid OSM place name. 
    :type place_name: str
    :return: A GeoDataFrame containing building data for the specified place. If no data is found, None is returned.
    :rtype: gpd.GeoDataFrame
    """

    tags = {'building': True} # Get only buildings
    cprint(f"Start: Retrieving geodata for: {place_name}")
    # Get the geometry of the place
    try:
        gdf_raw_geo = ox.features_from_place(place_name, tags)
        try:
            # remove rows with "man_mande" not emtpy, if this column exists
            gdf_raw_geo = gdf_raw_geo[gdf_raw_geo['man_made'].isna()]
        except:
            pass
        #reset index                                              
        gdf_raw_geo.reset_index(inplace=True)
        cprint(f"Done: Geodata for {place_name} successfully retrieved.", "green")
        return gdf_raw_geo
    except Exception as e:
        cprint(f"Error: {e}", "red")
        return None

def get_geodata_from_polygon(polygon: shapely.geometry.polygon.Polygon) -> gpd.GeoDataFrame:
    """Get geodata from a polygon using the OSMnx library.

    This function retrieves geodata from a polygon using the OSMnx library. The data is filtered for buildings.

    :param polygon: A shapely polygon object representing the area of interest. The polygon must be a valid shapely polygon.
    :type polygon: shapely.geometry.polygon.Polygon
    :return: A GeoDataFrame containing building data for the specified polygon. If no data is found, None is returned.
    :rtype: _type_
    """
    tags = {'building': True} # Get only buildings
    cprint(f"Start: Retrieving geodata for the {polygon}")
    # Get the geometry of the place
    try:
        gdf_raw_geo = ox.features_from_polygon(polygon, tags)
        try:
            # remove rows with "man_mande" not emtpy
            gdf_raw_geo = gdf_raw_geo[gdf_raw_geo['man_made'].isna()]
        except:
            pass
        #reset index 
        # set a numeric index
        gdf_raw_geo.reset_index(inplace=True)
        cprint(f"Done: Geodata for the polygon {polygon} successfully retrieved.", "green")
        return gdf_raw_geo
    except Exception as e:
        cprint(f"Error: {e}", "red")
        return None


def geodesic_point_buffer(lat: float, lon: float, km: float) -> list:
    """Perform a geodesic point buffer around a given latitude and longitude.

    :param lat: Latitude of the center point.
    :type lat: float
    :param lon: Longitude of the center point.
    :type lon: float
    :param km: Radius of the buffer in kilometers.
    :type km: float
    :return: A list of coordinates representing the buffer around the point.
    :rtype: list
    """
    # Azimuthal equidistant projection
    aeqd_proj = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0")
    tfmr = Transformer.from_proj(aeqd_proj, aeqd_proj.geodetic_crs)
    buf = Point(0, 0).buffer(km * 1000)  # distance in metres
    return transform(tfmr.transform, buf).exterior.coords[:]

def polygon_by_circle(lat: float, lon: float, radius: float) -> shapely.geometry.polygon.Polygon:
    """Generate a polygon representing a circle around a given latitude and longitude.

    :param lat: Latitude of the center point.
    :type lat: float
    :param lon: Longitude of the center point.
    :type lon: float
    :param radius: Radius of the circle in kilometers.
    :type radius: float
    :raises ValueError: If the radius is negative or greater than 20 km.
    :return: Returns a polygon representing the circle in the format of a shapely polygon.
    :rtype: shapely.geometry.polygon.Polygon
    """

    if radius < 0:
        raise ValueError("Radius must be positive")
    if radius > 20:
        raise ValueError("Radius must be less than 20 km")

    geo_circle = geodesic_point_buffer(lat, lon, radius)

    polygon = Polygon(geo_circle)  
    
    return polygon

def extract_relevat_data(gdf_in: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Extract the relevant data from the raw geodata that are needed for further analysis. 
    
    Data that are not available are set to a default value, and a warning is printed. Observe the output in the console to check for missing data and data quality.

    :param gdf_in: GeoDataFrame with the raw geodata obtained from OSM
    :type gdf_in: gpd.GeoDataFrame
    :return: A new GeoDataFrame containing the relevant data for further analysis.
    :rtype: gpd.GeoDataFrame
    """

    # extract the relevant data from the raw geodata that are needed for further analysis
    cprint("Start: Extracting relevant data from the raw geodata")
    # copy the gemetry coulumn
    print(gdf_in.head())
    gdf_out = gdf_in[['geometry']].copy()

    # add a unique id for each building
    gdf_out['building_id'] = 'building_' + gdf_in.index.astype(str)

    # add the addr: information if available
    if 'addr:city' in gdf_in.columns:
        gdf_out['addr:city'] = gdf_in['addr:city']
        cprint("Added addr:city", "green")
    else:
        gdf_out['addr:city'] = None
        cprint("Warning: addr:city not found in the raw data", "yellow")

    if 'addr:postcode' in gdf_in.columns:
        gdf_out['addr:postcode'] = gdf_in['addr:postcode']
        cprint("Added addr:postcode", "green")
    else:
        gdf_out['addr:postcode'] = None
        cprint("Warning: addr:postcode not found in the raw data", "yellow")

    if 'addr:street' in gdf_in.columns:
        gdf_out['addr:street'] = gdf_in['addr:street']
        cprint("Added addr:street", "green")
    else:
        gdf_out['addr:street'] = None
        cprint("Warning: addr:street not found in the raw data", "yellow")
    
    if 'addr:housenumber' in gdf_in.columns:
        gdf_out['addr:housenumber'] = gdf_in['addr:housenumber']
        cprint("Added addr:housenumber", "green")
    else:
        gdf_out['addr:housenumber'] = None
        cprint("Warning: addr:housenumber not found in the raw data", "yellow")
    
    # add building information if available (according to the OSM wiki)
    if 'building' in gdf_in.columns:
        gdf_out['building'] = gdf_in['building']
        cprint("Added building", "green")
    else:
        gdf_out['building'] = None
        cprint("Warning: building not found in the raw data", "yellow")

    if 'building:levels' in gdf_in.columns:
        gdf_out['building:levels'] = gdf_in['building:levels']
        cprint("Added building:levels", "green")   
    else:
        gdf_out['building:levels'] = None
        cprint("Warning: building:levels not found in the raw data", "yellow")

    if 'height' in gdf_in.columns:
        gdf_out['height'] = gdf_in['height']
        cprint("Added height", "green")
    else:
        gdf_out['height'] = None
        cprint("Warning: height not found in the raw data", "yellow")

    if 'building_flats' in gdf_in.columns:
        gdf_out['building_flats'] = gdf_in['building_flats']
        cprint("Added building_flats", "green")
    else:
        gdf_out['building_flats'] = None
        cprint("Warning: building_flats not found in the raw data", "yellow")

    if 'construction_date' in gdf_in.columns:
        gdf_out['construction_date'] = gdf_in['construction_date']
        cprint("Added construction_date", "green")
    else:
        gdf_out['construction_date'] = None
        cprint("Warning: construction_date not found in the raw data", "yellow")

    cprint("Done: Relevant data extracted from the raw geodata", "green")
    return gdf_out  


def estimate_data_osm(gdf_in: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Estimate the required data for the subsequent analysis (e.g. number of floors) just from the OSM data. 
    
    The function estimates the required data for subsequent analysis. Where not sufficient data is available, the data is estimated from the available data. The estimation is based on the OSM data and might not be accurate, depending on the quality of the OSM data.
    
    :param gdf_in: GeoDataFrame containing the relevant data, exracted from the raw geodata.
    :type gdf_in: gpd.GeoDataFrame
    :return: A new GeoDataFrame containing the estimated building properties for further analysis.
    :rtype: gpd.GeoDataFrame
    """
    cprint("Start: Processing data for building classification")
    gdf_out = gdf_in[['building_id','geometry']].copy()

    # get construction year
    gdf_out['year_of_construction'] = gdf_in['construction_date']
    gdf_out.loc[gdf_out['year_of_construction'].isna(), 'year_of_construction'] = -99 # set a numeric value for missing data eaysier handling
    # ensure that the year of construction is an integer
    gdf_out['year_of_construction'] = gdf_out['year_of_construction'].astype(int)


    data_avail_year_of_construction = 1 - gdf_in['construction_date'].isna().sum() / len(gdf_in)
    cprint(f"Data availability for year of construction: {data_avail_year_of_construction*100:.2f} %")

    # calculate the projected area of the building
    gdf_out['projected_ground_area'] = gdf_in['geometry'].to_crs(epsg=3035).area

    # calculted for how mny buildings the projected area is available
    data_avail_area = (gdf_out['projected_ground_area'] > 0).sum() / len(gdf_out)
    cprint(f"Data availability for projected ground area: {data_avail_area*100:.2f} %")

    average_floor_height = 3.5 # average floor height in m; this is a rough estimation and can be adapted
    small_building_aspect_ratio = 2.5 # average ratio of 
    large_building_aspect_ratio = 12 # average ratio of length to width for large buildings; 
    # tbd: a case distinction between small and large buildings could be implemented to improve 

    # get the number of floors 
    for index, row in gdf_in.iterrows():
        try:
            # try to get the actual number of floors first
            gdf_out.at[index, 'number_of_floors'] = int(row['building:levels']) 
        except:
            try:
                if math.isnan(row['height']) is False:
                    # as alternative estimate the number of buildings by the average height of a floor
                    gdf_out.at[index, 'number_of_floors'] = round(row['height'] / average_floor_height, 0)
                else:
                    # if no information is available, assume the height based on a average ground area to height ratio
                    # make a distinction between small and large buildings
                    if gdf_out.at[index, 'projected_ground_area'] < 1500:
                        gdf_out.at[index, 'number_of_floors'] = round(math.sqrt(gdf_out.at[index,'projected_ground_area']) / (small_building_aspect_ratio*average_floor_height),0)
                    else:
                        # for large buildings, assume a higher aspect ratio
                        gdf_out.at[index, 'number_of_floors'] = round(math.sqrt(gdf_out.at[index,'projected_ground_area']) / (large_building_aspect_ratio*average_floor_height),0)
            except:
                # if no information is available, assume the height based on a average ground area to height ratio
                if gdf_out.at[index, 'projected_ground_area'] < 1500:
                    gdf_out.at[index, 'number_of_floors'] = round(math.sqrt(gdf_out.at[index,'projected_ground_area']) / (small_building_aspect_ratio*average_floor_height),0)
                else:
                    # for large buildings, assume a higher aspect ratio
                    gdf_out.at[index, 'number_of_floors'] = round(math.sqrt(gdf_out.at[index,'projected_ground_area']) / (large_building_aspect_ratio*average_floor_height),0)

    # set the number of floors to 1 if the estimated number of floors yielded 0; zero number of floors would make the area zero as well
    gdf_out.loc[gdf_out['number_of_floors'] == 0, 'number_of_floors'] = 1

    data_avail_number_of_floors = 1 - gdf_in['building:levels'].isna().sum() / len(gdf_in)
    cprint(f"Data availability for number of floors: {data_avail_number_of_floors*100:.2f} %")

    # print average and max number of floors
    cprint(f"Average number of floors: {gdf_out['number_of_floors'].mean():.2f}")
    cprint(f"Maximum number of floors: {gdf_out['number_of_floors'].max():.2f}")

    # calculate the number of dwellings
    for index, row in gdf_in.iterrows():
        try: 
            gdf_out.at[index, 'number_of_dwellings'] = int(row['building_flats']) 
        except:
            # if no information is available, assume the number of dwellings based on the size of the building
            gdf_out.at[index, 'number_of_dwellings'] = math.ceil(gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] / 250) # assuming 250 m² per dwelling; including that there is a lot of projected area the is  not residnetial area
    
    data_avail_number_of_dwellings = 1 - gdf_in['building_flats'].isna().sum() / len(gdf_in)
    cprint(f"Data availability for number of dwellings: {data_avail_number_of_dwellings*100:.2f} %")

    # get the building category according to the OSM definition: https://wiki.openstreetmap.org/wiki/Key:building

    # split up multiple entries first
    for index, row in gdf_in.iterrows():
        multiple_entries = row['building'].split(';')
        if len(multiple_entries) == 0:
            gdf_out.at[index, 'building_primary'] = 'house' # default value as that is the most freuequently used building type
            gdf_out.at[index, 'building_secondary'] = 'None'
        elif len(multiple_entries) == 1:
            gdf_out.at[index, 'building_primary'] = multiple_entries[0]
            gdf_out.at[index, 'building_secondary'] = 'None'

        else:
            # check if one entry is a garage or carport and assign it to the secondary building type
            if 'carport' in multiple_entries[0] or 'garage' in multiple_entries[0] or 'garages' in multiple_entries[0]  or 'parking' in multiple_entries[0]: 
                gdf_out.at[index, 'building_secondary'] = 'garage'
                gdf_out.at[index, 'building_primary'] = multiple_entries[1]
            else:
                gdf_out.at[index, 'building_primary'] = multiple_entries[0]
                gdf_out.at[index, 'building_secondary'] = multiple_entries[1]


    #gdf_in['building_primary'] = gdf_in['building'].str.split(';', expand=True)[0] 
    #gdf_in['building_secondary'] = gdf_in['building'].str.split(';', expand=True)[1] 

    # iterate through the building types and assign a category
    ratio_heated_projected_area = 0.6 # ratio of heated area to projected area; this value is based on a rough estimation and can be adapted
    ratio_heated_projected_area_PCI = 0.1 # ratio of heated area to projected area for PCI buildings; to account for less heating in huge industry sites; this value is based on a rough estimation and can be adapted
    cap_heated_area_SFH = 300 # maximum heated area for a single family house; to account e.g. for farm houses, with larege area but only partially heated; this value is based on a rough estimation and can be adapted

    for index, row in gdf_out.iterrows():
        try:
            match row['building_primary']:
                case 'apartments':
                    gdf_out.at[index, 'building_category'] = 'AB'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'barracks':
                    gdf_out.at[index, 'building_category'] = 'TH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'bungalow': 
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'cabin':
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'detached':
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'annexe':  
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'dormitory':
                    gdf_out.at[index, 'building_category'] = 'AB'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'farm':
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'ger':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
                case 'hotel':
                    gdf_out.at[index, 'building_category'] = 'AB' # assuming a hotel has a similar energy demand as an apartment building
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'house':
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'houseboat':
                    gdf_out.at[index, 'building_category'] = 'SFH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                        gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                case 'residential':
                    if gdf_out.at[index, 'number_of_dwellings'] < 2:
                        gdf_out.at[index, 'building_category'] = 'SFH'
                        if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                            gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                    elif gdf_out.at[index, 'number_of_dwellings'] < 5:
                        gdf_out.at[index, 'building_category'] = 'MFH'
                    else:
                        gdf_out.at[index, 'building_category'] = 'AB'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'semidetached_house':
                    gdf_out.at[index, 'building_category'] = 'TH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'static_caravan':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
                case 'stilt_house':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
                case 'terrace':
                    gdf_out.at[index, 'building_category'] = 'TH'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case 'tree_house':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
                case 'trullo':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
                case 'commercial':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'industrial':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'kiosk':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0 
                case 'office':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'retail':  
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'supermarket':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'warehouse':
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
                case 'civic':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'college':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'fire_station':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'government':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'hospital':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'kindergarten':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'museum':  
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'public':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'school':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'university':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'sports_hall':
                    gdf_out.at[index, 'building_category'] = 'PCI'
                    gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area_PCI
                case 'yes': # this can cause problems, as the 'yes' tag is used very inconsistently in OSM, and therefore some buildings can be missmatched!
                    if gdf_out.at[index, 'number_of_dwellings'] < 1:
                        gdf_out.at[index, 'building_category'] = 'other'
                        gdf_out.at[index, 'heated_area'] = 0
                    elif gdf_out.at[index, 'number_of_dwellings'] < 2:
                        gdf_out.at[index, 'building_category'] = 'SFH'
                        gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                        if gdf_out.at[index, 'heated_area'] > cap_heated_area_SFH:
                            gdf_out.at[index, 'heated_area'] = cap_heated_area_SFH
                    elif gdf_out.at[index, 'number_of_dwellings'] < 5:
                        gdf_out.at[index, 'building_category'] = 'MFH'
                        gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                    else:
                        gdf_out.at[index, 'building_category'] = 'AB'
                        gdf_out.at[index, 'heated_area'] = gdf_out.at[index, 'projected_ground_area'] * gdf_out.at[index, 'number_of_floors'] * ratio_heated_projected_area
                case default:
                    gdf_out.at[index, 'building_category'] = 'other'
                    gdf_out.at[index, 'heated_area'] = 0
        except:
            gdf_out.at[index, 'building_category'] = 'other'
            gdf_out.at[index, 'heated_area'] = 0
            
    

    # decrease heating area by parking space which is not heated
    gdf_out.loc[gdf_out['building_secondary'] != 'None','heated_area'] = gdf_out.loc[gdf_out['building_secondary'] != 'None', 'heated_area'] * 0.5
    
    cprint("Done: Data processing for building classification", "green")
    return gdf_out


def merge_other_data(gdf_in: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """This function is a dummy to merge in other data sources like cadastre data if available.

    If more accurate building data is available, it can be merged in here. The function is a placeholder for future development and does not do anything at the moment.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :return: A new GeoDataFrame containing the updated and merged building data.
    :rtype: gpd.GeoDataFrame
    """
    gdf_out = gdf_in.copy()
    # add code here to merge in other data sources e.g. more accurate building data from the cadastre

    return gdf_out


def adjust_num_of_dwelling(gdf_in: gpd.GeoDataFrame):
    """Set the number of dwellings to 0 for non residential buildings and limit the number of dwellings to a maximum of 500.

    The number of dwellings is set to 0 for non residential buildings (e.g. PCI) and the number of dwellings is limited to a maximum of 500. This is done to avoid unrealistic values for the number of dwellings.
    In the subsequent analysis, the number of dwellings is used to calculate the specific heating demand and the yearly heating demand.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    """
    #remove the number of dwelling for non residential buildings
    gdf_in.loc[gdf_in['building_category'] == 'PCI', 'number_of_dwellings'] = 0
    gdf_in.loc[gdf_in['building_category'] == 'other', 'number_of_dwellings'] = 0
    
    # TBD: make that parameterizable
    # limit the number of dwellings to a maximum of 500
    gdf_in.loc[gdf_in['number_of_dwellings'] > 500, 'number_of_dwellings'] = 500

def remove_zero_area_buildings(gdf_in: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Removes buildings with a projected area of 0.

    For buildings with zero area, no further analysis is possible. The buildings are removed from the GeoDataFrame to avoid errors in the subsequent analysis.
    Most buildings without a projected area might be buildings that are not residential or not heated.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :return: A new GeoDataFrame containing the building data without buildings with a projected area of 0.
    :rtype: gpd.GeoDataFrame
    """        
    #cprint("Start: Removing buildings with a projected area of 0")
    # delete the building rows with a projected area of 0; when the area is not available at all, no other estimation is possible
    len_before = len(gdf_in)
    gdf_out = gdf_in[gdf_in['projected_ground_area'] > 0].copy()
    len_after = len(gdf_out)
    cprint(f"Done: Removed {len_before-len_after} buildings with a projected area of zero", "green")

    return gdf_out
  

def get_spec_HD(gdf_in: gpd.GeoDataFrame, path_spec_HD: str):
    """Get the specific heating demand of the buildings. 

    The specific heating demand is obtained from the excel file containing the specific heating demand data. 
    The country speccific data can be obtained from the TABULA webtool: https://webtool.building-typology.eu/#bm and has to be adjusted to the format of the excel file.

    :param gdf_in: GeoDataFrame containing the building data, including the year of construction and the building category.
    :type gdf_in: gpd.GeoDataFrame
    :param path_spec_HD: Path to the excel file containing the specific heating demand data.
    :type path_spec_HD: str
    """

    # load data for specific heating demand
    spec_HD = pd.read_excel(path_spec_HD, index_col=[0,1])

    # iterate trough all rows and columns in the data
    for year in spec_HD.index:
        for bulding_type in spec_HD.columns:
        #print(row, column, data.loc[row, column])
            gdf_in.loc[(gdf_in['building_category'] == bulding_type) & (gdf_in['year_of_construction'] >= year[0]) & (gdf_in['year_of_construction'] <= year[1] ), 'specific_HD'] = spec_HD.at[year, bulding_type]


def get_yearly_HD(gdf_in: gpd.GeoDataFrame):
    """Calculate the yearly heating demand based on the specific heating demand and the heated area

    :param gdf_in: GeoDataFrame containing specific heating demand and heated area.
    :type gdf_in: gpd.GeoDataFrame
    """
    gdf_in['YearlyDemand'] = gdf_in['specific_HD'] * gdf_in['heated_area'] 


def calc_heating_hours(case_study_name: str, config: dict, Tset = 21.0, Tsetback=18.0) -> float:
    """Calculate the heating degree hours based on the outside temperature and the setpoint temperature.
     
    The heating degree hours are calculated for the whole year, for a reference actitive occupancy profile. 

    :param case_study_name: Name of the case study, used to load the temperature data
    :type case_study_name: str
    :param config: Config dictionary
    :type config: dict
    :param Tset: Setpoint temperature for buildings, defaults to 21
    :type Tset: float, optional
    :param Tsetback: Setback temperature for buildings, defaults to 18
    :type Tsetback: float, optional
    :return: Number of heating degree hours for the whole year in °C*h 
    :rtype: float
    """
    df_temp = load_temp_data(case_study_name, config)

    # calculate a generic Tset profile for the year that is Tset from 6 - 22h and Tsetback from 22 - 6h
    df_temp['hour'] = df_temp.time.dt.hour

    df_temp['Tset'] = Tset
    df_temp.loc[df_temp['hour'] < 6, 'Tset'] = Tsetback
    df_temp.loc[df_temp['hour'] > 21, 'Tset'] = Tsetback

    df_temp['Tdiff'] = df_temp['Tset'] - df_temp['T_out']

    heatinghours = df_temp.loc[df_temp['Tdiff'] > 0, 'Tdiff'].sum() 

    return heatinghours


def estimate_thermal_properties(gdf_in: gpd.GeoDataFrame, heatinghours=50000.0, speHeatStorCap = 0.04, surface_to_area = 2.5):
    """    Estimate the thermal properties of the buildings based on the specific heating demand and the heated area. 
    
    The estimated thermal properties are scaled up for the whole building, that means all parameters refere to the total building.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :param heatinghours: Number of heating hours per year. Sumed up the temperature difference between setpoint and outside temperature for all hours of the year., defaults to 50000
    :type heatinghours: float, optional
    :param speHeatStorCap: specific heat storage capacity of the building material in kWh/m²K of a wall of average thickness, defaults to 0.04
    :type speHeatStorCap: float, optional
    :param surface_to_area: float ratio of thermal active area of the building (i.e. walls, roof, floor) to the ground area of the building (avoid double counting of indoor walls), defaults to 2.5
    :type surface_to_area: float, optional
    """

    gdf_in['GeneralisedThermCond'] = gdf_in['YearlyDemand'] / heatinghours
    gdf_in['GeneralisedThermCap'] = gdf_in['projected_ground_area'] * gdf_in['number_of_floors'] * surface_to_area * speHeatStorCap
    gdf_in['MaxDemand'] = gdf_in['GeneralisedThermCond'] * 25 + gdf_in['GeneralisedThermCap'] * 2 #assuming the max heating power to heat at 25 °C temp. diff, and be able to rise temp by 2 °C per hour

def define_setpontTemp(gdf_in: gpd.GeoDataFrame, temp_setpoint = 22.0, std_setpoint = 2.0, temp_setback = 17.0, std_setback = 2.0):
    """Define the setpoint temperature and the setback temperature for the buildings. 
    
    The values are randomly generated based on a normal distribution around the mean values. If real data is available, this function can be replaced by the real data.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :param temp_setpoint: mean value for the setpoint temperature, defaults to 22.0
    :type temp_setpoint: float, optional
    :param std_setpoint: standard deviation for the setpoint temperature, defaults to 2.0
    :type std_setpoint: float, optional
    :param temp_setback: mean value for the setback temperature, defaults to 17.0
    :type temp_setback: float, optional
    :param std_setback: standard deviation for the setback temperature, defaults to 2.0
    :type std_setback: float, optional
    """

    gdf_in['temp_setpoint'] =  random.normal(temp_setpoint, std_setpoint, len(gdf_in))
    gdf_in['temp_setback'] =  random.normal(temp_setback, std_setback, len(gdf_in))


def estimate_local_heat_prod_costs(gdf_in: gpd.GeoDataFrame, cost_per_kWh = 0.15):
    """Estimate the local heating production costs based on the cost per kWh.

    The costs are randomly generated based on a normal distribution around the mean value. If real data is available, this function can be replaced by the real data.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :param cost_per_kWh: mean value for the heating costs per kWh, defaults to 0.15
    :type cost_per_kWh: float, optional
    """

    gdf_in['LocalHeatProdCosts'] = abs(random.normal(cost_per_kWh, 0.02, len(gdf_in)))
        

def estimate_internal_gainfactor(gdf_in: gpd.GeoDataFrame, gain_per_dwelling = 0.12):
    """Estimate the internal gain factor for the buildings.

    The gain is scaled per dwelling, with the input parameter gain_per_dwelling. 

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :param gain_per_dwelling: value for the internal gain per dwelling in kW/dwelling, defaults to 0.2
    :type gain_per_dwelling: float, optional
    """

    gdf_in['InternalGainFactor'] = gdf_in['number_of_dwellings'] * gain_per_dwelling


def estimate_solar_gainfactor(gdf_in: gpd.GeoDataFrame, window_fraction = 0.08):
    """Estimate the solar gain factor for the buildings. 
    
    The gain is scaled per dwelling, with the input parameter window_fraction. If more accurate data is available, this function can be replaced by the real data.

    :param gdf_in: GeoDataFrame containing the building data.
    :type gdf_in: gpd.GeoDataFrame
    :param window_fraction: share of window area of total facade area, defaults to 0.1
    :type window_fraction: float, optional
    """

    # estimate the area of one side of the building and multiply it with the share of the window area
    gdf_in['SolarGainFactor'] = gdf_in['number_of_floors'] * gdf_in['projected_ground_area'].apply(lambda x: math.sqrt(x)) * window_fraction


def write_buildingdata_to_disk(gdf_in: gpd.GeoDataFrame, casestudy: str, config: dict):
    """Write the building data to disk as a GeoJSON file.

    The function checks if the directory for the case study exists and creates it if not. The building data is then written to disk in the specified format.
    :param gdf_in: GeoDataFrame containing the building data
    :type gdf_in: gpd.GeoDataFrame
    :param casestudy: name of the casestudy
    :type casestudy: str
    :param config: configuration dictionary
    :type config: dict
    """
    
    # check if directory exists
    data_path = os.path.join(casestudy)
    if not os.path.exists(data_path):
        os.makedirs(data_path)
    gdf_in.to_file(os.path.join(data_path,config['building_data']['buildings_gdf']), driver='GeoJSON')
    cprint("Done: Building data written to disk", "green")
    
   

def generate_complete_geodataset(casstudy_name: str, location) -> gpd.GeoDataFrame: 
    """Run a complete case study for a given location. 
    
    The function generates a new case study folder if the folder does not exist yet. 
    It then loads the building data from OpenStreetMap and processes it to get the relevant data.
    The function estimates the thermal properties of the buildings and writes the data to disk.
    The function returns the processed building data as a GeoDataFrame.
    
    :param casstudy_name: Name of the case study
    :type casstudy_name: str
    :param location: Geographical location of the case study. Can be a polygon (must be polygon) or a place name (string).
    :type location: str or shapely.geometry.polygon.Polygon
    :return: A GeoDataFrame containing the building data for the case study with all properites.
    :rtype: gpd.GeoDataFrame
    """
    
    # load config
    config = load_config()

    # generate a new case study folder if not exists
    generate_new_case_study(casstudy_name, config)

    # check if the location is a polygon or a place name
    if isinstance(location, shapely.geometry.polygon.Polygon):
        gdf_raw_geo = get_geodata_from_polygon(location)
    else:
        gdf_raw_geo = get_geodata_from_place(location)

    # Extract the relevant data
    gdf_relevant_data = extract_relevat_data(gdf_raw_geo)
    # Estimate the data
    gdf_estimated_data = estimate_data_osm(gdf_relevant_data)
    # Merge other data
    gdf_merged = merge_other_data(gdf_estimated_data)
    # remove buildings with a projected area of 0
    gdf_complete = remove_zero_area_buildings(gdf_merged) 

    # adjust the number of dwellings for non residential buildings
    adjust_num_of_dwelling(gdf_complete)
    # Get the specific heating demand
    path_spec_HD = os.path.join(casstudy_name,config['parameter_dir'], config['building_data_dir'], config['case_study_data']['building_typology'])

    get_spec_HD(gdf_complete, path_spec_HD)
    # Get the yearly heating demand
    get_yearly_HD(gdf_complete)
    # estimate the number of heating hours in the year
    heatinghours = calc_heating_hours(casstudy_name, config)

    # Estimate the thermal properties
    estimate_thermal_properties(gdf_complete, heatinghours)
    # Define the setpoint temperature
    define_setpontTemp(gdf_complete)
    # Estimate the local heating production costs   
    estimate_local_heat_prod_costs(gdf_complete)
    # Estimate the internal gain factor
    estimate_internal_gainfactor(gdf_complete)
    # Estimate the solar gain factor
    estimate_solar_gainfactor(gdf_complete, 0.1)
    # Write the building data to disk
    write_buildingdata_to_disk(gdf_complete, casstudy_name, config)

    return gdf_complete


if __name__ == "__main__":
    casestudy = "Frauental"
    location = "Frauental, Styria, Austria"
    
    # create a polygon for the location
    generate_complete_geodataset(casestudy, location) 
