#!/usr/bin/python


class AppendMortalityData:

    def __init__(self):
        from pyspark.sql import SparkSession
        import os

        self.psql_user = os.environ['POSTGRESQL_USER']
        self.psql_pw = os.environ['POSTGRESQL_PASSWORD']

        self.spark = SparkSession \
            .builder \
            .appName("Write mortality data to DB") \
            .getOrCreate()

    def get_states(self, d):
        # from pyspark.sql.types import StringType
        # from pyspark.sql.functions import lpad

        file = 's3a://data-engineer.club/aux/state_codes.csv'
        states = d.spark.read.csv(file, inferSchema=True, header=True)
        states = states.select(states.number.alias('state_num_code'),
                               states.code.alias('state'))
        states.show()
        return states

    def create_schema(self):

        from pyspark.sql.types import StructType, StructField,\
            IntegerType, StringType, DateType

        schema = StructType([
            StructField('state', IntegerType(), True),
            #StructField('state_num', IntegerType(), True),
            StructField('county_fips', IntegerType(), True),
            StructField('date', DateType(), True),
            # StructField('weekday', IntegerType(), True),
            # StructField('manner', IntegerType(), True),
            StructField('number', IntegerType(), True)
        ])

        return schema

    def load_field_positions(self, vintage):
        """
        Read from a JSON file the schema for a given mortality year

        :param vintage: Year for which to retrieve schema
        :return: Dictonary of starting position and length
            for each exiting field of that year
        """

        import json
        f = open('mort_schema.json', 'rt')
        j = json.load(f)
        return j[str(vintage)]

    def transform_to_schema(self, df, schema):
        """
        Splits a fixed-with string dataframe into a specified schema

        :param df: Inout dataframne
        :param schema: Dictionary of starting position and length for each field
        :return: Dataframe with columns
        """
        from pyspark.sql.types import StringType
        from pyspark.sql.functions import lit

        df = df.select(
            df.value.substr(schema['state_s'], schema['state_l']).alias('state'),
            df.value.substr(schema['county_s'], schema['county_l']).alias('county_fips'),
            # df.value.substr(schema['year_s'], schema['year_l']).alias('year'),
            df.value.substr(schema['month_s'], schema['month_l']).alias('month'),
            # df.value.substr(schema['day_s'], schema['day_l']).alias('day')
            # df.value.substr(schema['weekday_s'], schema['weekday_l']).alias('weekday'),
            # df.value.substr(schema['manner_s'], schema['manner_l']).alias('manner'),
        )

        df.printSchema()

        df2 = df
        df2.show(10)
        return df2

    def get_county_concordance(self, vintage):
        import math
        decade = int(math.floor (vintage / 10.0)) * 10

        file = 's3a://data-engineer.club/crosswalk/xw' + str(decade) + '.csv'
        xwalk = d.spark.read.csv(file, inferSchema=True, header=True)
        xwalk = xwalk.select(xwalk.fips_state.alias('st_orig'),
                             xwalk.fips_county.alias('co_orig'),
                             xwalk.fips_state_1990.alias('st_ref'),
                             xwalk.fips_county_1990.alias('co_ref'))
        xwalk = xwalk.withColumn('st_orig', xwalk.st_orig / 10) \
                     .withColumn('co_orig', xwalk.co_orig / 10) \
                     .withColumn('st_ref', xwalk.st_ref / 10) \
                     .withColumn('co_ref', xwalk.co_ref / 10)

        xwalk.show()
        return xwalk

    def process_year(self, d, vintage):

        from pyspark.sql.types import IntegerType
        from pyspark.sql import functions as F
        from pyspark.sql.functions import concat, unix_timestamp, to_date, lit
        from pyspark.sql.functions import when

        spark = d.spark

        # Read the input file from S3
        file = 's3a://data-engineer.club/mort/mort' + str(vintage) + '.txt'
        df = spark.read.text(file)
        #df.show(20)

        # Extract fields from fixed-width text file
        field_pos = self.load_field_positions(vintage)
        df2 = self.transform_to_schema(df, field_pos)
        #df2.show(20)

        # Get year from the filename, not from the content
        vintage_str = str(vintage)

        # Deal with unknown days encoded as 99
        if 'day_s' in self.load_field_positions(vintage):
            df2 = df2.withColumn('day', when(df2.day >31, '01').otherwise(df2.day))
        # And with missing day for newer years
        else:
            df2 = df2.withColumn('day', lit('01'))

        # Create date from year, month, and day
        df2 = df2.withColumn('date', to_date(unix_timestamp(
            concat(lit(vintage_str), df2.month, df2.day), 'yyyyMMdd').cast('timestamp')))

        # Transform historical county number into 1990 ref standardized number
        df2 = df2.withColumnRenamed('county_fips', 'county_fips_orig')

        xwalk = self.get_county_concordance(vintage)
        df2 = df2.join(xwalk, [df2.state == xwalk.st_orig,
                               df2.county_fips_orig == xwalk.co_orig])

        df2.printSchema()

        #df2 = df2.select()


        # Transform state numbers into abbreviations
        # if field_pos['state_format'] == 'num':
        #     df2 = df2.withColumnRenamed('state', 'state_num')
        #     states = self.get_states(d)
        #     states.show(55)
        #     df2 = df2.join(states, df2.state_num == states.state_num_code, how='left')
            # df4.filter(df4.state == '42').show(50)

        # print(schema['year_l'])
        # if schema['year_l'] == 2:
        #    df2 = df2.withColumn('year', concat(lit('19'), df2.year))

        #
        #df2.show(24)
        #
        # # Convert types
        df3 = df2.withColumn('state', df2['state'].cast(IntegerType())) \
                 .withColumn('county_fips_orig', df2['county_fips_orig'].cast(IntegerType())) \
                 .withColumn('co_ref', df2['co_ref'].cast(IntegerType())) \
                 .withColumn('month', df2['month'].cast(IntegerType())) \
                 .withColumn('day', df2['day'].cast(IntegerType()))

        df3 = df3.withColumnRenamed('co_ref', 'county_fips')


        # # Sum up death counts for each combination of parameters
        df3 = df3.groupby(df3.state, df3.county_fips, df3.date) \
            .agg(F.count(df3.date).alias('number')) \
            .sort(df3.state, df3.county_fips, df3.date)
        #
        df3.show(34)
        #
        # #dft = df3.filter(df3.month == 5)
        # #print(dft.show(20))
        return df3

    def main(self, d, first_vintage, last_vintage):

        if first_vintage > last_vintage:
            raise Exception("First year cannot be after the last year")
        if (first_vintage < 1968) or (first_vintage > 2018):
            raise Exception("First year out of range")
        if (last_vintage < 1968) or (last_vintage > 2018):
            raise Exception("Last year out of range")

        main_df = d.spark.createDataFrame([], self.create_schema())
        #main_df.show()

        # Generate inclusive list of years
        vintages = range(first_vintage, last_vintage + 1)
        for vintage in vintages:
            print(vintage)
            new_df = self.process_year(d, vintage)
            new_df.show(24)
            main_df = main_df.union(new_df)

        main_df.show(28)

        main_df.write \
            .format("jdbc") \
            .mode("append") \
            .option("url", "jdbc:postgresql://10.0.0.14:5432/heatwave") \
            .option("dbtable", "mortality") \
            .option("user", self.psql_user) \
            .option("password", self.psql_pw) \
            .option("driver", "org.postgresql.Driver") \
            .save()

        d.spark.stop()

        return


if __name__ == "__main__":
    import sys

    d = AppendMortalityData()
    first_vintage = int(sys.argv[1])
    last_vintage = int(sys.argv[2])
    d.main(d, first_vintage, last_vintage)
